# Ingestion prompt-test log — diejuristin corpus, started 2026-10-24

A recurring check that runs every three hours while the big `diejuristin`
ingestion job (361,819 files, started 2026-10-24 08:55 UTC after a backend
restart) is in progress. Each tick asks questions about documents ingested since
the previous tick, probes retrieval with sentences taken from those documents,
scans the container logs, and records anything worth fixing or building.

## How it runs

- Script: `scripts/ingestion-prompt-test/prompt_test.py` (subcommands `tick`,
  `ask`, `search`, `sample`, `doc`; `--help` explains the options).
- State and raw results: `C:\RecallHub-Verify\bench\prompt-test-2026-10-24\`
  (`state.json`, `tick-NN.json`, `tick-NN.md`, `asks-NN.jsonl`).
- Schedule: a Claude Code session cron job (`23 */3 * * *`, local time) runs the
  tick and appends to this file. Session cron jobs expire after seven days, so the
  loop ends on 2026-10-31 unless re-created.
- Authentication: a test API key named `claude-prompt-test-loop …` on the
  superadmin account (id `ab288df1-f55c-4ac7-ae33-0f127d88abce`, expires
  2026-11-01). It lives only in the bench folder. Revoke it on the API-keys page
  or delete the `api_keys` document once the loop is over.
- Chat prompts go through `POST /api/v1/sessions/{id}/messages` (the federated
  agent, same path as the UI) in fresh sessions that are deleted afterwards.
  Retrieval probes use `/search/hybrid`, `/search/semantic` and `/search/text`.

Manual run:

```powershell
$env:PYTHONUTF8 = "1"; uv run python scripts/ingestion-prompt-test/prompt_test.py tick
```

## Findings backlog

Severity: **high** = throughput or data loss, **medium** = wrong or missing
results the user will notice, **low** = noise or polish. "Seen" lists the ticks
that reproduced the finding.

| # | Sev | Finding | Seen |
|---|-----|---------|------|
| F1 | high | **Throughput collapsed 20× after the restart with concurrency 16, and timed-out conversions keep running.** Inserts fell from ~2,000 documents per 30 min (07:30–08:30, `max_concurrent_files` 2) to ~100 per 30 min after 08:55 (`max_concurrent_files` 16, `thread_pool_workers` 16). In 2.1 h the job logged 209 per-file timeouts at 902 s, but 16 slots × 126 min cannot even hold 209 × 15 min, and the longest logged conversion took 2,160 s against a 902 s limit. 211 timeouts fell into the single hour 10:14–11:14 UTC. Tick 2: not one timeout between 11:02 and 13:50, and inserts recovered to 1,500–1,800 per 30 min from 12:30 on, once the batch of ~170 heavy scanned Grundbuch/Firmenbuch extracts and their zombie retries had drained. So the collapse is triggered per batch of slow files rather than permanent, but every such batch costs hours and the mechanism is unchanged. The timeout is `asyncio.wait_for` around `run_in_executor` (`src/ingestion/ingest.py:3939`): on timeout the slot is released and the file is queued for a retry with a 1.5× budget, but the thread keeps converting, so more than 16 conversions run at once, every file slows down, more files time out, and the retries redo the work. The API process sits at ~1,350–1,500 % CPU with 28–31 GB RSS. ETA from the status endpoint: 26.8 days at tick 1, 13.1 days at tick 2 (at the pre-restart rate it would be ~3.5 days). Fix: run conversions in killable worker processes (the `process_isolation_enabled` flag is stored but nothing in `src/ingestion` reads it), or make the per-file budget scale with concurrency / measure CPU time, and cap concurrency at what the CPU can serve (20 cores in the container; `OCR_OMP_THREAD_LIMIT` is empty). Tick 3: one timeout in 3 h (905 s), 30 files/min, ETA 8.1 d. Tick 4: one timeout, 31 files/min, ETA 7.8 d. Tick 6 (after the resume, F23): 553 timeouts on 451 files in 4 h, 18 files/min, ETA 13.7 d, the same slow scans as on day one. Tick 7: the retry tail drained, 5 timeouts in 3 h, 18 files/min, ETA 13.0 d. Tick 8: 0 timeouts, 19 files/min, ETA 12.2 d. Tick 9: 10 timeouts, 18 files/min, ETA 12.4 d. Tick 10: 1 timeout, 17.8 files/min, ETA 12.7 d. Tick 11: 1 timeout, 17.5 files/min, ETA 12.8 d. Tick 12: 5 timeouts, 17.0 files/min, ETA 13.1 d. Tick 13: 6 timeouts, 16.4 files/min, ETA 13.4 d. Tick 14: 2 timeouts, 15.8 files/min, ETA 13.8 d. Tick 15: 9 timeouts, 15.3 files/min, ETA 14.2 d. Tick 16: 8 timeouts, 14.7 files/min, ETA 14.6 d (the rate has fallen every tick since tick 8). Tick 17: 11 timeouts, 14.1 files/min, ETA 15.2 d. Tick 18: 19 timeouts on 16 files (Energieausweis scans among them), 13.7 files/min, ETA 15.6 d. Tick 19: 23 timeouts on 19 files, 13.2 files/min, ETA 16.1 d; the backend now idles at 0.5 % CPU while the embedder runs at 100 % (F29). Tick 20: 25 timeouts on 21 files, 12.6 files/min, ETA 16.8 d (277 documents per hour). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: process-isolated conversions killed on timeout (PR #25)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22 |
| F2 | high | **Per-file stats and failures are only persisted when the job ends.** `_save_ingestion_stats` (`ingest.py:4139`) writes `ingestion_stats` and `failed_documents` after the loop, so for a multi-week job the failed-documents page, the analytics endpoints and the retry options (`retry_timeouts`, `retry_no_chunks`) have nothing to work with, and a restart loses everything accumulated in memory. The `rag_diejuristin` database has neither collection. Fix: flush `file_stats` in batches (every N files or minutes). Tick 5: the redeploy at 22:56 UTC discarded 14 h of in-memory stats (212 timeouts, ~700 no-chunk files) for good. Tick 6: because nothing was persisted, the resumed job re-queued every one of them (F23). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: stats and failed_documents flushed every 25 files / 60 s (PR #25)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 1, 5, 6 |
| F3 | medium | **Job status counters stay at zero.** `timeout_files`, `no_chunks_files`, `error_files`, `image_only_pdfs` and `failed_files` all read 0 in `GET /ingestion/status` and in the job document while the log shows 209 timeouts and 471 no-chunk files in the same window. `processed_files` (1,179) also counts no-chunk files and duplicates, so "documents 1,172" does not match the 319 documents actually inserted. Tick 5: after the resume the same fields read `null`. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: job counters follow the written stats (PR #25)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 1, 5 |
| F4 | medium | **Copies first seen in the same run are all ingested.** The content-hash de-duplication compares against `existing_hashes` loaded once at job start (`ingest.py:3718`); hashes of files processed during the run are never added, so every copy that was not already in the database gets converted, embedded and stored. Tick 1 window: 36 duplicate groups, 65 redundant documents out of 319 (20 %); today overall: 2,007 groups, 2,974 redundant documents. Besides the wasted OCR, the copies crowd search results (the control question returned `Gelöscht/…` and the live copy of the same file). Tick 2: 477 groups / 710 redundant documents out of 4,231 (17 %). Fix: add the hash under the results lock after each successful file; show copies as one result with several locations. Tick 3: 1,268 groups / 1,845 redundant of 8,400 (22 %); the chat's 10 source slots held only 2 distinct files for the Honorarnote and KSV questions. Tick 4: 904 groups / 1,278 redundant of 5,878 (22 %). Tick 5: 890 / 1,233 of 4,824 (26 %). Tick 6: 434 / 625 of 2,894 (22 %). Tick 7: 502 / 796 of 3,291 (24 %). Tick 8: 458 / 673 of 3,606 (19 %). Tick 9: 434 / 658 of 2,923 (23 %). Tick 10: 404 / 573 of 2,623 (22 %). Tick 11: 380 / 554 of 2,783 (20 %). Tick 12: 386 / 554 of 2,357 (24 %; one letter stored 12 times across client folders). Tick 13: 291 / 436 of 2,073 (21 %). Tick 14: 302 / 427 of 1,946 (22 %). Tick 15: 219 / 311 of 1,761 (18 %). Tick 16: 216 / 307 of 1,585 (19 %); three copies of one template file filled 9 of the 10 source slots of a chat answer. Tick 17: 191 / 309 of 1,307 (24 %); one scan (`SKMBT_C224e18041813150.pdf`) stored in four folders. Tick 18: 197 / 300 of 1,300 (23 %). Tick 19: 165 / 236 of 1,121 (21 %). Tick 20: 128 / 195 of 832 (23 %). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: same-run copies deferred and skipped (PR #25)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 41, 42 |
| F5 | medium | **Lookup by document title or file name fails.** The text search index covers only `chunks.content` (`dynamic: false`); `metadata.title` and `metadata.source` exist on every chunk but are not indexed, and the chat has no title-match stage. "Fasse das Dokument "Grundbuchabfrage, KG 11029, EZ 52" zusammen" returned "nicht enthalten" although three copies exist; `/search/text` and `/search/hybrid` for the title return unrelated documents. Fix: index `metadata.title` and `metadata.source` (with a path analyzer) in `text_index`, boost title matches, or prepend the title to the contextualized chunk text. Tick 3: "Sonstige Erledigung, Beschluss-1-3" not found, a different "Protokoll, 2-3" summarised instead. Tick 4: the ERV file name "SonstigeErledigung_19058_91561_2018_03_29" not found either. Tick 5: `MagdicsBallLi_PetrPe/DZ, Aktenbestandteil, Klagsrückziehung.pdf` is not found by its title words either; its key figures ("wegen: 3 EUR 13.340,14", a Kostenverzeichnis of label/value fragments) are split across chunk boundaries, so neither text nor vector search surfaces it. Tick 7: "Vorbereitende Schriftsatz der beklagten Partei_55500" not found, the agent summarised "similar procedure types" instead. Tick 8: "Ladung_21921_106616_2021_01_12" not found, honest refusal. Tick 9: "DZ, Beilage, 28Cg 34_14x - Replik Blg_2" not found, and this time the agent summarised the sibling "DZ, Sonstiges, Schriftsatz-4" as if it were the requested document (F20). Tick 10: "Schummer_Kriwanek,_Das_neue_Unternehmensgese…" not found, honest. Tick 11: a counter-example, the ZVB 2020/27 article was found and summarised because its title words recur in the body; titles work only when they do. Tick 13: "Ärztliches Zeugnis" (a medical certificate for an adult-representation registration) not found; the agent described the document type from Vorsorgevollmacht templates instead, without saying the document itself was missing (F20). Tick 14: asked for "Direktzustellung_23198_114708_…", it summarised the ERV sibling "…114707" (the cover note of the same delivery) as if it were the requested file. Tick 16: a Nutzwertgutachten was "summarised" from the surveyor's cover letters and a later Wohnungseigentumsvertrag of the same matter, not from the Gutachten itself. Tick 17: "Klient Einheitssatz, Abrechnung bis 12_03_2019" not found; the agent said so and listed similar Einheitssatz Abrechnungen of other clients, with the disclaimer. Tick 19: "IMG_20210920_0005" (a phone scan) not found, honest refusal after 298 s. Tick 20: "Fotomontage samt E-Mail" was described from the Klagebeantwortung drafts that cite it as evidence, not from the document (F20). | 1, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 16, 17, 19, 20, 21, 22, 24, 25, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 42, 43 |
| F6 | medium | **Unreadable files are stored as garbage text.** `M U S T E R/MANZ/manztexte/…/html/*.htm` (1999, 425 KB) is not HTML at all (high-bit bytes from offset 0, probably an obfuscated MANZ format); it is decoded with replacement characters, chunked and embedded, and it shows up in search results. 123 documents are more than 5 % U+FFFD; 563 documents contain at least one (500 PDFs, 63 htm). Fix: after decoding, treat text with more than a few percent replacement characters as unreadable (`no_chunks` with a clear reason), and log the `bs4.dammit` warning once per file. Tick 3: `KLEIN PS-CIORA/Attachements/Mahnschreiben-Ciora-Vasile.pdf` carries "����" in its letterhead. Tick 10, a variant without replacement characters: `Gesetzestexte/Europa/EU Gründungsvertrag.pdf` reads "GR˜NDUNG DER EUROP´ISCHEN GEMEINSCHAFT", a PDF whose font maps umlauts to the wrong glyphs; text search for "Gründung" cannot find it. | 1, 3, 10 |
| F7 | medium | **The API process is starved by in-process ingestion.** UI-started jobs run inside the backend container (`runner: api`); the worker container idles. Consequences in 2 h: 7 `Request timeout … GET /health`, one `504` on `/ingestion-queue/queue`, slow-request warnings for `/ingestion/status` and `/system/host-stats`, and 8 `Rate limit exceeded … api_ingestion` hits caused by the Ingestion page's own polling. The Docker health check (`curl -f /health`, 10 s, 5 retries) still passes but sits one busy minute away from marking the container unhealthy. Tick 2: RSS grew from 28.4 to 30.7 GB; no health-check timeouts in this window (no timeouts either, see F1). Fix: hand UI-started jobs to `backend/workers/ingestion_worker.py`, and exempt the UI's status polling from the ingestion rate limit. Tick 3: RSS 32.5 GB (28.4 → 30.7 → 32.5). Tick 4: 34.0 GB. Tick 5: 30.8 GB just before the redeploy, so it fluctuates rather than leaks monotonically. Tick 6: 35.3 GB only 4 h after the fresh start, so the bulk is reached quickly (zombie conversions and their page images). Tick 7: 39.0 GB, a new high 7 h after the restart. Tick 8: 38.0 GB. Tick 9: 42.0 GB, and `GET /health` itself hit the 30 s request timeout 4 times (HTTP 504 to the Docker health check, which tolerates 5 failures in a row before marking the container unhealthy). Tick 10: 37.0 GB. Tick 11: **44.4 GB**, the highest yet, at 1,435 % CPU; MongoDB at 131 % CPU in the same snapshot. Tick 12: 42.1 GB. Tick 13: 40.9 GB. Tick 14: 37.9 GB. Tick 15: 36.2 GB. Tick 16: 39.6 GB. Tick 17: **45.5 GB**, a new high 37 h after the restart. Tick 18: 44.2 GB. Tick 19: 40.3 GB. Tick 20: 37.5 GB, and `GET /health` timed out 4 more times. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: UI-started jobs are queued for the ingestion worker when one is alive (`INGESTION_UI_JOB_RUNNER`, default auto); pause/resume/stop reach it through `control_command`); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. Tick 35: 14 `GET /health` timeouts in 3 h, the most since tick 23. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38 |
| F8 | low | **OSD failures are logged at ERROR for near-blank pages** (235 in 2 h, "Too few characters. Skipping this page"). They are expected for cover sheets and blank scans; downgrade to DEBUG or count them per file. Tick 2: 3,796 in 2.8 h, 23 ERROR lines per minute. Tick 3: 4,553 in 3 h. Tick 4: 5,406. Tick 5: 6,315. Tick 6: 6,233. Tick 7: 7,120. Tick 8: 7,125. Tick 9: 6,757. Tick 10: 7,844. Tick 11: 7,708. Tick 12: 7,074. Tick 13: 7,075. Tick 14: 9,018 (50 per minute). Tick 15: 7,509. Tick 16: 7,827. Tick 17: 8,020. Tick 18: 7,740. Tick 19: 8,781. Tick 20: 7,543. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F9 | medium | **471 "No chunks created" warnings in 2 h; most are expected-empty files, but image-only scans are among them and vanish without a record.** Expected: Outlook `ATT0000x.txt` placeholders (38 + 23 + 20 + 19 …) and MANZ frameset pages (`i_stichwort`, `i_inhalt`, `suchen`, `searchbnd1`). Not expected: `EDELS-BAUM/Attachements/Kontoauszug Sparkasse OÖ 15_07_2020.PDF` is a one-page image-only PDF (4 image objects, no text layer, 595×420 pt) that Docling "converted" in 3.8 s with no text, i.e. OCR did not run on it or returned nothing; it is now neither a document nor a recorded failure (F2), so nobody will retry it. Fix: classify empty text/HTML files as "empty" without a warning; for PDFs with zero text and ≥ 1 image, force full-page OCR and, if that still yields nothing, record `image_only_pdf` in `failed_documents`. Tick 2: 33 no-chunk files, plus 15 stored documents under 200 characters that are image placeholders (`<!-- image -->` with a few OCR fragments, e.g. a floor plan, an SMS screenshot). Tick 3: 53 no-chunk files, 40 documents under 200 chars. Tick 4: 69 and 61. Tick 5: 56 and 42 (web-page prints such as `P-8-08/HANDY Parken.pdf` reduce to menu labels). Tick 6: 697 no-chunk files, 163 of them the Outlook `ATT0000x` placeholders processed a second time after the resume. Tick 7: 58 no-chunk files, 59 documents under 200 chars. Tick 8: 72 and 63 (photo collections such as `KaceFrVU/Bilder Teil 3.pdf`, `5. Fotos Wohnung_1`). Tick 9: 79 and 47 (a 3-character document: `WESA-ATLANTIS/Attachements/Kabelbrücke Beispiel 4.pdf` = "2.0"). Tick 10: 92 and 46 (an e-card scan, a birth certificate, an auction notice). Tick 11: 88 and 58 (a 47-character `Krankenakte.pdf`, a Geburtenbuch scan, a sketch). Tick 12: 70 and 45, among them three copies of a passport scan reduced to its machine-readable zone (`MANDL-KV/Attachements/Reisepass ….pdf`), i.e. identity-document data in the search index. Tick 13: 91 and 33. Tick 14: 92 and 54. Tick 15: 67 and 45. Tick 16: 69 and 35, plus another passport scan reduced to its machine-readable zone (`WlazAg_WlazPe/Ausweise.pdf`) and savings-book scans. Tick 17: 59 and 24. Tick 18: 118 no-chunk files (scanned court exhibits "DZ, Beilage …" that OCR to nothing) and 51 tiny documents, among them a residence-permit card (`FP-GLORI-VWSTRAF/Attachements/AUT.pdf`). Tick 19: 52 and 23. Tick 20: 29 and 17. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: pure scans are recorded as `image_only_pdf` (OCR found nothing) or `image_only_pdf: true` (OCRed), counters `image_only_pdfs` / `image_only_pdfs_ocr`, summary `image_only_pdfs_ocr_ok`; empty files log at INFO); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F10 | low | **Chunks exceed `max_tokens` after contextualisation.** 2,487 of 6,262 new chunks are 513–514 tokens (the heading context is prepended after the 512-token split). Harmless with the llama.cpp embedder (`-c 32768`), but the budget should include the context. | 1, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F11 | low | **Chat costs ~50 s and 13–17 k prompt tokens per question** (10 sources × ~435-token chunks into gemma4:12b, 1 iteration). Acceptable for "thinking", too slow for "fast". Also, the non-streaming `POST /sessions/{id}/messages` returns `thinking.search.operations` empty, so the searches the orchestrator ran are only visible in `agent_trace.orchestrator_steps`. Tick 3: fast mode answered the KSV question in 15 s versus 61 s in auto mode with the same core facts, but reports 0 tokens in `stats` and `agent_trace`. Tick 4: judgment question 22 s fast versus 103 s auto (2 iterations), same facts and the same wrong reasoning (F20). Tick 6: first clear divergence, fast mode (8 s) answered the expert-fee question with the case's Streitwert (EUR 34.000) while auto mode (78 s) was exactly right; fast mode's single search without reformulation misses the decisive document more often. Tick 7: fast mode correct on the deposit-refund e-mail (42 s under load versus 54 s auto). Tick 8: fast mode (55 s) claimed the GmbH guide "names no minimum capital" because it only received the guide's table-of-contents chunk, while auto mode (71 s) found the EUR 35.000 sentence. Tick 9: fast mode correct and richer than auto on the WEG-Novelle article, 42 s versus 205 s. Tick 10: fast mode right on five of six lease figures (48 s), auto mode on all six plus a date discrepancy between offer and contract (138 s). Tick 11: fast mode correct on a technical statement (37 s). Tick 12: fast mode (58 s) got total and instalments right but declared the Kautionsguthaben absent; auto mode (67 s) had all three and the acceptance conditions. Tick 13: both modes equal on the Honorarnote (both missed its amounts table), fast 83 s versus auto 138 s; auto mode took 274 s on the remediation offer. Tick 14: both modes equal again on the § 146 commentary (same absence claim), fast 98 s versus auto 112 s. Tick 15: fast 76 s versus auto 229 s (2 iterations) for the same header facts; the other auto question timed out at 300 s. Tick 16: both modes fully correct on the Urheberrecht template claim, auto 73 s, fast 131 s (fast is not always faster under load). Tick 17: both modes fully correct on § 157 UGB, fast 78 s versus auto 195 s. Tick 18: both hand-crafted auto questions timed out at 300 s; fast mode answered the air-conditioner opinion in 146 s with the core points (OGH 5 Ob 24/08b, the "fast notwendig" standard, location and shading alternatives) but without the § 16 WEG criteria list and the second decision. Tick 19: auto mode timed out on the lease question; fast mode (95 s) got the object and deposits but declared start, duration and rent absent. Tick 20: fast mode (192 s) honestly reported that only the opinion's heading was available, auto mode (152 s) answered with the opposite conclusion from another document; here the honest non-answer was the better one. | 1, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F12 | low | **Letter-soup OCR on some scans**, e.g. `M U S T E R/EHE/Berechnung Unterhalt Ehegatte_Kinder.pdf` → "PP Non: 2 FU 9 fu tte 72 7 ImenDgur vavadig rey piadmyo". A per-document quality score (vowel-less long tokens, alphanumeric share) would let the UI flag such documents for re-OCR at a higher DPI. Tick 2: `P-4-12/webErv/2012-11-02 Beilage B.PDF` ("elenbechen Allardrm ZI T8 Terrace eo"). Tick 3: a CAD plan (`P-76-11/Pläne Elk/Einreichplan …`) and a Meldezettel scan. Tick 4: `FehrSt_GomeMa/Unterlagen/Kostenvoranschlag S_2.pdf` ("ae tr? RE”"). Tick 5: `Metka_SchwLu/DZ, Beilage, 28Cg 34_14x - Replik Blg_5.pdf` ("Ayyesadoysod adeL TL OTOZ'TT'ST", an upside-down scan), `WeisJo_HaibMa/Weiss Unterlagen1.pdf`. Tick 6: handwritten notes (`SperWo_Unter/sperlich _2_.pdf`, `SandEl_SETür/Vermerk Besprechung.pdf`, `ErseYu_Taxi/Erses.pdf`) become word salad; a handwriting detector could mark them "handschriftlich" instead of indexing noise. Tick 7: a handwritten testament (`P-51-18/Attachements/Scan0190.pdf`, 155 chars of noise) and a client-meeting note (`SchaMa_RomaOl/Konf_ mit Mandanten.pdf`), documents a lawyer would want flagged rather than silently unreadable. Tick 8: `SeebJo_GarzKa/1550818581655.pdf` came out as one letter per line (vertical or rotated text layout, not OCR noise). Tick 9: bank statements scanned upside down come out as mirrored digit strings ("00'0 €6'999 00'0 95'259", `F-Erzbis_Covid19/Albin Denk/Mietzinse Kontoauszüg EG 3.pdf`, `ER-2-20/Attachements/SKMBT_22321051213220.pdf`), so the OSD rotation step did not catch them; plus a sick note and a vehicle-disposal confirmation as letter soup. Tick 10: handwritten notes again (`ErdiAl_ES/Erdinc Notizen.pdf`). Tick 11: two more upside-down bank statements (`ERZBI-WEIDI/…/SKMBT_22321080912540.pdf`, `ER-2-20/…/SKMBT_22320082010120.pdf`) and a cellar plan. Tick 12: 13 of 300 sampled, incl. another upside-down statement, a vehicle registration and a creditor list. Tick 14: 8 of 300, incl. an upside-down invoice scan (`HIRSC-PAAR/Attachements/IMG_20220102_0001.pdf`) and a key-handover protocol. Tick 15: handwritten client-meeting notes (`SchaCh_Scheidung/…/Scharf - Mitschrift Dr_ Trautmann.pdf`, `P-37-05/1988-08-04_KONFL_m_Mdt.pdf`) and a hand-filled custody calendar. Tick 16: `HollPe_Uniqa/Unterlagen/1621923533865.pdf` is 16,149 characters of one letter per line (a rotated or columnar layout read letter by letter), i.e. 16 k characters of unsearchable text embedded as if it were prose; plus two copies of a mine plan as digit soup. Tick 17: 10 of 300, incl. a handwritten accident report and another passport copy (`BÖHM-KV/Attachements/Pass Pale Kopie.pdf`). Tick 18: 9 of 300, mostly plans (an HKLS building-services plan, house plans) and an upside-down scan. Tick 19: 10 of 300, again plans and price lists; a bachelor thesis was flagged on its table of contents only (heuristic false positive). Tick 20: 9 of 300, construction plans and two handwritten letters (`P-3-21/Beilagen/2021-07-13 Handschriftl Anzeige ….pdf`). | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 30, 33, 38, 40, 41 |
| F13 | info | **Retrieval retention is fine for distinctive sentences**: hybrid hit@5 = 10/12, hit@1 = 6/12; semantic-only hit@5 = 5/12 (exact-sentence probes favour the text index). The two misses were form boilerplate shared by hundreds of Firmenbuch extracts; the probe picker now skips sentences found in ≥ 4 documents. Tick 2: hybrid 5 of 6 in the top 5 (6 of 12 probes were lost to F14), 12 boilerplate candidates skipped. Tick 3: hybrid 8 of 12 in the top 5 and 10 of 12 in the top 10, semantic 5 of 12. Tick 4: hybrid 7 of 9 answered probes in the top 5 (3 lost to 504s), semantic 2 of 6 (6 lost). Tick 5: only 2 probes answered (the rest hit the redeploy, F23); 1 of 2 hybrid, 2 of 2 semantic in the top 5. Tick 6: hybrid 6 of 9, semantic 6 of 10 (5 lost to 504s). Tick 7: hybrid 7 of 11, semantic 6 of 11; 16 boilerplate candidates skipped. Tick 8: only 4 hybrid and 6 semantic probes answered (the rest 504), 3 of 4 and 3 of 6 in the top 5. Tick 9: 3 answered per index, all at rank 1; 18 of 24 lost to 504s. Tick 10: 2 answered per index, 20 of 24 lost. Tick 11: 8 answered per index, 7 of 8 in the top 5 for both (6 at rank 1). Tick 12: hybrid 5 of 9, semantic 5 of 10 in the top 5. Tick 13: 18 of 24 lost to 504s, 1 of 2 and 1 of 4 answered in the top 5. Tick 14: 21 of 24 lost, the 3 answered all in the top 2; 21 boilerplate candidates skipped. Tick 15: **0 of 24 answered**, every probe search timed out. Tick 16: 0 of 24 again. Tick 17: 1 of 24 (semantic, rank 1). Tick 18: 0 of 24. Tick 19: 0 of 24. Tick 20: 0 of 24, the fifth blackout. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F14 | medium | **The search API rate limit (30 requests per minute per client IP, `api_search` in `backend/core/security.py`) is too low for authenticated clients and is shared by everyone behind the LAN gateway.** Tick 2's probes received 18 HTTP 429 responses (`retry_after` 21 s) after 24 requests in a few seconds; every LAN user reaches the backend through the Caddy gateway's container IP, so one bucket serves the whole office unless the middleware honours the forwarded client address. Fix: key the limiter by user or API key when authenticated, read `X-Forwarded-For` from the gateway, and raise the search limit. The test script now waits and retries once on 429. Tick 12: the limiter stalled the probes three times (6–13 s waits). Tick 13: three more stalls. | 2, 12, 13, 21, 22, 26, 29 |
| F15 | medium | **Citations name only the document title, and titles are shared across cases.** 456 documents are titled "Sonstige Erledigung, Beschluss", 365 "Ladung, Ladung zu einem bestimmten Termin", 30 "Sonstige Erledigung, Kostenbestimmung für Drittschuldnererklärung". The tick-2 title-summary question was answered from `StahRu_Darlehen/…` although the window's document of that title was `DohnEl_Todic/…`, with no hint that 30 such documents exist; the follow-up question listing them attributed them to titles like "SonstigeErledigung_14800_67943_2015_07_30" instead of the case folders. Fix: include the case folder (first path segment, the Akt) in `[Source: …]` citations and in the sources list, and let the agent say when a title is ambiguous. Tick 4: "Sonstige Erledigung, Urteil, 2" exists in `11_FeiBad_1/` and `Zürich_AnsaEv/`; the answer mixed both under one citation (F20). Tick 5: asked for "the" Vorschreibung of Geylinggasse 11, the agent answered from one of 49 unit ledgers in the case folder without saying so (correct for that unit, 383,99, but the asked-about unit pays 284,43). Tick 12, a positive example: asked to summarise "Bescheid über die Festsetzung von Aussetzungszinsen", the agent said that several such Bescheide of different taxpayers exist and listed each with its own period and amount, which is the behaviour this finding asks for; it still had no way to name the case folders. Tick 15: "Unfallbericht" (a generic title) was answered as a survey of several accident reports, again without saying which case each belongs to. | 2, 3, 4, 5, 12, 15, 23, 24, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F16 | low | **Tiny chunks from form layouts.** 1,214 of 32,795 new chunks (3.7 %) are under 20 tokens (one form label per paragraph: "Gericht:", "am:", "Beginn:"); they produce weak embeddings and pad the vector index. Fix: merge sub-20-token chunks into their neighbours (HybridChunker `merge_peers`) or drop label-only chunks. Tick 3: 1,853 of 74,283 (2.5 %). Tick 4: 1,589 of 63,808. Tick 5: 968 of 56,441 (1.7 %). Tick 6: 628 of 38,132. Tick 7: 1,097 of 44,734. Tick 8: 1,092 of 45,953. Tick 9: 766 of 39,264 (2.0 %). Tick 10: 702 of 40,736. Tick 11: 758 of 39,144. Tick 12: 659 of 35,056. Tick 13: 911 of 34,440 (2.6 %). Tick 14: 639 of 33,824. Tick 15: 502 of 28,566. Tick 16: 714 of 28,395 (2.5 %). Tick 17: 429 of 25,481. Tick 18: 429 of 26,451. Tick 19: 395 of 22,594. Tick 20: 318 of 19,072. | 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F17 | medium | **Case numbers (Aktenzeichen) are tokenisation-sensitive and the agent fills the gap with another case.** Asked about "Strafsache 13 U 39/16v" (typed without the space the court prints), hybrid search does not return the hearing protocol `CZ-1835/webErv/SonstigeErledigung_16092_75279_2016_04_13.pdf` in its top 10; with "13 U 39/16 v" it is rank 4 (text search rank 2). The agent then answered from the sibling cover letter and presented cost-ledger lines dated 15.03.2016 from an unrelated case (`StahRu_VU/Stahl - VU HN.pdf`) as supporting details. Fix: extract case numbers at ingestion into a normalised keyword field (spaces removed) and expand queries both ways; when a question names an Aktenzeichen or case folder, restrict synthesis to documents of that case and say when nothing matched. Tick 10: "4 Cg 65/21b" (spelled as printed) retrieved the case's Folgeeingaben, Protokoll and ERV receipts but not the Schriftsatz with the Streitwert; the agent said so instead of guessing. Tick 11: "14 C 995/23z" and "KG 01660 EZ 858" (both as printed) retrieved the right filings and the answers were complete, so the failures are specific to spelling variants and to form fragments, not to identifiers as such. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: identifier-aware retrieval (PR #28)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 2, 10, 11, 30 |
| F18 | high | **Search latency climbs to 7–13 s while ingestion embeds.** Query embeddings go through the same llama.cpp server (`embedder-gpu`, `-np 4`, 8,192-token slots) as the ingestion batches of 100 chunks; at tick 3 every slot was `is_processing`, semantic search took 8.5–13.0 s server-side (text search 47 ms; 0.6 s at tick 2), hybrid probes had a median of 7.3 s, and chat latency rose to 60–68 s. Fix: a dedicated slot or second embedder instance for interactive queries, smaller ingestion batches (`embedding_batch_size`) while users are active, or a priority queue in `src/ingestion/embedder.py`. Tick 4: hybrid median 16.7 s, semantic 23.8 s, and 9 searches (6 semantic, 3 hybrid) were cut off by the backend's 30 s request timeout (`REQUEST_TIMEOUT_SECONDS` in `backend/main.py`) with HTTP 504; chat 83–103 s. Search is effectively unavailable while ingestion embeds. Tick 5: 4 more 504s in the minutes before the redeploy. Tick 6: medians 16.3 s hybrid / 15.1 s semantic, 5 × 504; the control question failed for the first time because the Firmenbuchabfrage was not among the sources. Tick 7: medians 11.5 s hybrid / 7.4 s semantic, 2 × 504, chat 110–135 s. Tick 8 (worst so far): 14 of 24 probe searches hit the 30 s limit, medians 30.0 s / 27.9 s, chat 48–111 s. (The single `OpenAI API error: 401` in `backend.routers.system` at 06:31 UTC is a provider test from the System page with the empty OpenAI key, not the embedder.) Tick 9: 18 of 24 probe searches timed out, medians 30.0 s for both indexes; scripted chat 62–129 s, hand-crafted auto-mode questions 163–205 s (a two-iteration answer spent 169 s in workers). Tick 10: 20 of 24 probe searches timed out (18 HTTP 504 lines in the log). Tick 11: medians back to 2.7 s with the embedder at 40 % CPU (text-heavy literature needs fewer embeddings per minute), still 8 of 24 timeouts; the latency tracks the embedder's load, not the corpus size. Tick 12: medians 4.9 s hybrid / 1.2 s semantic, 5 of 24 timeouts, embedder back at 100 %. Tick 13: 18 of 24 timeouts, medians 30 s, chat 79–172 s, one hand-crafted auto answer 274 s (284 s in workers), the slowest of the run. Tick 14: 21 of 24 probe searches timed out (the most yet), scripted chat 45–264 s. Tick 15: **24 of 24 timed out**, the first complete blackout of the HTTP search path; chat (which embeds in-process without the 30 s limit) still answered in 154–156 s, but one hand-crafted auto-mode question hit the sessions endpoint's 300 s limit and returned HTTP 504, the first chat timeout of the run. Tick 16: second consecutive full blackout (24 of 24), scripted chat 127–139 s, and a second hand-crafted auto-mode question lost to the 300 s chat limit. Tick 17: 23 of 24 timeouts, scripted chat 169–223 s, hand-crafted auto answers 195 and 268 s. Tick 18: 24 of 24 timeouts, and the scripted title-summary chat itself hit the 300 s limit (HTTP 504), the first scripted chat lost; 3 of the 4 auto-mode chats of this tick exceeded 300 s, only fast mode still answers (146 s). Tick 19: 24 of 24 search timeouts; the two scripted chats finished at 297 and 298 s, one or two seconds under the limit; of the hand-crafted auto questions one timed out and one took 273 s. Tick 20: 24 of 24 search timeouts, and the control chat itself hit 300 s for the first time (HTTP 504). | 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F19 | low | **Doubled-letter text from overprinted PDFs.** Bold-by-overprint or shadow text is extracted as "B B E E Z Z I I R R K K S S G G E E R R I I C C H H T T" (`GASTEC-EG 1190/Attachements/20240618___VORBEREITENDER SCHRIFTSATZ.pdf`); 49 of 8,727 window documents and 191 of 52,585 overall match the pattern (Unternehmenskaufverträge, Grunderwerb forms, Rangordnungserklärungen). Fix: detect runs of doubled characters after text extraction and collapse them, or re-run such pages through OCR. Tick 10: two copies of `GASTEC-STEINBÖCK/Attachements/20241001_Vollmachtsbekanntgabe und Vertagungsbitte…` ("B BE EZ ZI IR RK KS SG GE ER RI IC CH HT T"). | 3, 10 |
| F20 | high | **Synthesis confuses fields and entities of same-type documents.** The Honorarnote question named the insured ("Ihr VN: Sila Schweißtechnik GmbH") as the addressee although the note is addressed to D.A.S. Rechtsschutz AG; in fast mode the KSV question added that "another document" shows rating 411, which is the KSV profile of Agrar-Brücke GmbH (`CZ-1855/898379409-101-7229299.pdf`), not Schilling Werbe; tick 2's case-number answer merged a cost ledger of another case the same way (F17). Fix: make the synthesis prompt state which entity each source is about and refuse to merge values from sources whose entity differs from the question's; show the case folder in citations (F15) so users can spot it. Tick 4 (raised to high): asked why the judgment 17 Cg 114/11a (`11_FeiBad_1/Sonstige Erledigung, Urteil, 2.pdf`) dismissed the claim, both agent modes answered "mangels passiver Klagslegitimation, § 52d Abs 6 ÄrzteG" and cited that title; the judgment contains neither phrase, the reasoning belongs to the same-titled judgment and pleadings of the unrelated case `Zürich_AnsaEv/`. Amounts and dates were right, the legal reason was invented from another case. Tick 6: fast mode reported "EUR 34.000,00 samt Anhang" (the Streitwert line of the case) as the amount paid to the expert. Tick 7 (minor): the fee-note answer listed the "außertourliche wesentliche Ermäßigung 3.000,00" (a discount) as "Ermächtigung" among the cost items, with the right figures. Tick 10: both modes said the *Vermieter* is bound to the Mietanbot until 31.03.2026, but the offer is the tenant's ("Ich stelle Ihnen … Mietanbot, an das ich mich … gebunden halte"), a party-role swap. Tick 17: asked for the payable amount of a contractor's Schlussrechnung (EUR 33.301,06 on the invoice), the agent reported EUR 29.193,34 "laut Schlussrechnung", which is the client's Rechnungsprüfbericht figure after a 3 % Haftrücklass; the figures are right, the document they belong to is not, and the discrepancy between invoice and audit went unmentioned. Tick 19: asked about the Rekursbeantwortung of 15.10.2018 in 5 MSch 13/15h (Rücklagenüberschuss, LG ZRS Wien 38 R 88/16x and 38 R 147/17z), the agent got the motion and the subject right, then continued with the arguments of a second "Rekursbeantwortung" in the same case folder (radiator maintenance, § 16 Abs 3 WEG) and never named the decisions asked for; fast mode mixed the Erstentwurf of a lease with its Konditionsblatt and a later version. Tick 20, the worst case so far: asked for the conclusion of the expert opinion GV_1/20 (which states "Photoepilation unterliegt nicht dem ÄrztInnen-Vorbehalt"), auto mode retrieved only its cover page and presented the opposite position from the firm's 2022 letter to the WKO ("dem Ärztevorbehalt unterfallend") as "the decisive legal classification" of the opinion. A reader would take away the reverse of what the Gutachten says. The Notariatsakt question was likewise padded with capital-increase clauses of two other companies. | 3, 4, 6, 7, 10, 17, 19, 20, 22, 24, 29, 30, 32, 33, 38, 40, 41 |
| F21 | low | **Mislabelled file formats fall through to the raw-text fallback.** `P-1322/Buchhaltungsjournal 2003.pdf` is a Word `.doc` with a `.pdf` extension: Docling sniffed it as MS Word, its msword backend demanded LibreOffice (not in the image, traceback in the log), the document was declared "not valid" and the raw-text fallback stored it. Real `.doc` files go through antiword (`/usr/bin/antiword` is present in both images; 129,159 `.doc` files wait on the shares). Fix: when Docling reports a format other than the extension, route by the sniffed format (antiword for `.doc`), not by the name. Also 2 genuinely invalid PDFs today (`RechnungLutz.pdf`, an OGH Volltext). Tick 6: `Fink INSOLVENZRECHT.pdf` (3 copies) and `FP-POGRI-LEITZ-PRIV/webErv/Beilage B.pdf` rejected by docling-parse, stored through the fallback. Tick 7: a `Grundbuch_….pdf` whose bytes match no Docling input format ("format None"), also through the fallback. Tick 8: `Storch Krankengeschichte EKH.pdf` "not valid". Tick 11: six copies of `w_leuthner_2020-09-16-140140.pdf` "not valid", all stored through the fallback (see F26). Tick 12: three more copies of the same file. Tick 14: four more (13 copies so far). | 4, 6, 7, 8, 11, 12, 14, 39 |
| F22 | low | **Absence claims are made from the retrieved chunks, not the document.** The Prafa policy question got the sums right but stated that the insurer "wird nicht explizit genannt", while the policy's last chunk is signed "Generali Versicherung AG" (and lists "Uniga [Uniqa] Österreich" as co-insurer). Fix: phrase absence as "in den abgerufenen Abschnitten", or fetch the remaining chunks of a document once it is identified as the answer's source. Tick 6: "kein spezifischer Streitwert genannt" for 33 Cg 31/26f although the order's "Wegen: EUR 87.000,00 samt Anhang" line sits in a small form-fragment chunk (F16) that was not retrieved. Tick 8: both modes said the GmbH guide does not define the function of the Stammkapital, although its own sentence "bildet einen Haftungsfonds für die Gläubiger" stands right next to the EUR 35.000 figure; auto mode "derived" functions from other sources instead. Tick 10: fast mode declared the Mietanbot's "mietfreie Zeit" absent while quoting the same document's rent figures from the contract summary instead. Tick 12: fast mode "keine Informationen über das Kautionsguthaben" although the demand letter's composition table lists "Kautionsguthaben inkl Zinsen EUR 6.024,33". Tick 13: both modes found the header chunk of a two-chunk Honorarnote (client, Causa, Bemessungsgrundlage) and declared its total absent; the second chunk is the five-line amounts table ("Honorar € 177,30 … Gesamtsumme € 220,30"), which neither the vector nor the text query reached. Small label/value tables are the systematic blind spot behind most absence claims (F16). Tick 14: both modes retrieved the "Dokumentinformation" header chunks of two editions of the WK StGB § 146 commentary (author, Stand) and declared the Strafdrohung absent, although the next chunk of each is the Normtext ("Freiheitsstrafe bis zu sechs Monaten oder Geldstrafe bis zu 360 Tagessätzen"). Fetching the neighbouring chunks of a hit document before synthesis would close most of these. Tick 15: same pattern on the GeKo Wohnrecht § 23 IO commentary (header retrieved, Normtext not); fast mode's wording "der eigentliche Inhalt ist nicht mitgeliefert" is the honest form this finding asks for, auto mode simply left the question unanswered. Tick 19: fast mode said the lease draft contains neither start, duration nor rent, while the Erstentwurf states 01.07.2017, 21 months and EUR 4.102 net; it had retrieved the object and deposit clauses only. Tick 20: for the 14-page Gutachten GV_1/20 both modes received only its cover-page chunk; fast mode said so ("lediglich die Überschrift"), auto mode filled the gap from another document (F20). Tick 43: two of six questions — a four-point memo reported as two points (`CZ-1999/27-08-09 AV.doc`, three fallback chunks) and a Honorarnote's Gesamtsumme 1.778,18 declared absent (`Klient Einheitssatz, Abrechnung bis 26_08_2021.docx`, the total in the fourth of four hybrid chunks); in both the first chunk of the right document was source 1 and its later chunks lost the ten chunk-level source slots to sibling documents of the same case. Fix: when a document is the top hit, pass its neighbouring chunks (or the whole document under a size cap) before the second-ranked document's chunks, and let the model say "the retrieved part does not mention" instead of "the document does not mention". | 4, 6, 8, 10, 12, 13, 14, 15, 19, 20, 24, 29, 30, 34, 37, 40, 41, 42, 43 |
| F23 | high | **A redeploy during a running job throws away its position and its in-flight files, and the resume retries every file that had failed.** At 22:56 UTC the backend, worker and frontend were recreated from new images (`recallhub-backend:db-stats`, built from main `fd8211b`, rollback tags `rollback-20261025`). The API marked the job interrupted and resumed it, which means: discovery of the 401,135 files runs again (minutes of share scanning), the incremental filter reloads 63 k sources, all counters restart at 0, the 16 conversions that were in flight are dropped without a record (F2), and clients saw "server disconnected" for about six seconds. Fix: a graceful drain (stop taking new files, let in-flight conversions finish, persist the queue index) on SIGTERM, and a resume that reuses the persisted file list instead of rescanning. Tick 6 (raised to high): the incremental filter only knows the documents collection, so every file that had timed out or produced no chunks before the restart is "new" again and, being small, sits at the front of the queue: 553 timeouts on 451 distinct files and 697 no-chunk files in the first 4 h after the resume, all 16 timeout files sampled before the restart among them, throughput down to 18 files/min (733 documents/h versus 1,600–2,800 before). Persisted per-file outcomes (F2) plus a "skip known failures unless retry is requested" filter would avoid it. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: a resumed job skips the files its own `failed_documents` records name unless a `retry_*` flag asks for that error type (`failed_paths_of_job`, pipeline and API queue build)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. Tick 27: the backend restart of 2026-10-28 00:30 UTC resumed job b98d150e once more (discovery 6 min, queue 297,198 files, counters re-based on the earlier runs); none of the 323 failures recorded in the next 2 h had an older record for the same path and no `ingestion_stats` row was written twice — the resume skips known failures and the registry as intended; the fix is verified, the full re-discovery remains. Tick 32: fourth re-discovery since the 10-26 deploy — the backend was recreated again at 19:21:49 UTC (same image `0ce9001`, only the backend container, 2.5 min after a performance-config save with `load_profile=office`, 16 slots — the reason is not recorded in any session note); the resumed queue re-sorts, so the books that had timed out start again from attempt 1/3. Tick 34: fifth re-discovery since 10-26 — the backend, worker and frontend were recreated at 21:37 UTC for the power-plan build (`bbafb3b`, branch `feature/ingestion-load-profiles`, PR #30 unmerged); discovery 15.5 min, queue 398,906 → 296,435, timed-out books back at the front once more. | 5, 6, 21, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39 |
| F24 | low | **No active Strategy OS spec for the Quellex tenant.** Every chat logs `no active spec for capability_id=legal_matter_qna tenant_id=quellex, falling back to legacy adapter`, so the config-driven runtime in `backend/agent/strategy/` is unused in production and the answers come from the legacy orchestrator. Either activate a spec from `backend/config/strategy_specs/` for the tenant or silence the warning. Tick 10: 5 more occurrences. Tick 13: 5 more. Tick 14: 5 more. Tick 15: 4 more. Tick 16: 4 more. Tick 17: 4 more. Tick 18: 4 more. Tick 19: 4 more. Tick 20: 4 more. | 6, 10, 13, 14, 15, 16, 17, 18, 19, 20, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 35, 36, 37, 38 |
| F25 | high | **Entity questions lose their best document as copies of related documents accumulate.** The control question ("ALPO CONSULT GmbH, FN 404945h, Geschäftszweig") was answered from the Firmenbuchabfrage (`F-BlatLj_GeweR/Firmenbuchabfrage, FN 404945h, ALPO CONSULT GmbH.pdf`, hybrid rank 1) at ticks 1–5 and failed at ticks 6 and 7: the extract still exists unchanged, but hybrid and text search now fill the top 10 with Finanzamt letters and a Ratenansuchen that carry "ALPO CONSULT GmbH" in their address block, each present 2–6 times (F4), plus Firmenbuch boilerplate of other companies, and `hybrid_search` only fetches `2 × match_count` candidates per index before RRF. Fix: collapse copies (same `content_hash`) before fusion, fetch 5–10× candidates (`numCandidates ≥ 20 × limit`), and give exact identifier hits (FN, EZ/KG, Aktenzeichen) a dedicated keyword field with a boost (ties in with F17). Tick 8: the extract was back among the 10 sources (below rank 5) and the answer gave only "Unternehmensberatung" instead of the full "Unternehmensberatung, Buchhaltung und Handel", so the ranking hovers at the edge of the window. Tick 9: wrong again ("Geschäftszweig … nicht explizit aufgeführt"), the extract absent from the sources, Ratenansuchen and Finanzamt copies on top. Tick 10: wrong again, same pattern (4 failures in the last 5 ticks). Tick 11: wrong again (5 of 6); PR #28 carries the fix but is not deployed. Tick 12: wrong again (6 of 7), this time Honorarnoten of the client on top. Tick 13: correct for once (the extract among the 10 sources, below rank 5), so the document sits right at the edge of the candidate window: 2 passes in the last 8 ticks. Tick 14: correct again (3 of the last 9), 214 s. Tick 15: half right ("Unternehmensberatung" only, from Honorarnoten; the extract not in the top sources). Tick 16: wrong ("Geschäftszweig nicht genannt", a letter's addressee presented as the company's manager). Tick 17: wrong again, 223 s. Tick 18: half right ("Beratung (Consulting)", inferred from a letter's address line, not from the extract). Tick 19: half right again ("Unternehmensberatung" from a Honorarnote), 297 s. Tick 20: no verdict, the control request timed out (F18); a hand-crafted question naming "FN 319646v" did not retrieve the Notariatsakt that carries the number (F17). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: identifier-aware retrieval with copies collapsed and wider candidate windows (PR #28)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. | 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 22 |
| F26 | medium | **Unreadable files are stored as documents whose text is the error message.** When Docling rejects a PDF and the raw-text fallback cannot read it either, the pipeline stores `[Error: Could not read file <name>]` as the document content, chunks it (1 chunk) and embeds it. 19 such documents exist (`P-106-10/Grundbuch_31.08.2012.pdf`, `P-1322/Buchhaltungsjournal 2003.pdf`, `…/RechnungLutz.pdf`, `KCZ-78/Geb1.pdf`, a Geschäftsverteilung of the HG Wien, six copies of `w_leuthner_2020-09-16-140140.pdf`, …). They count as ingested, so they are never retried, and a question that names the file gets an error string as its "source". Fix: treat a failed fallback as a failure (`error`/`invalid_format` in `failed_documents`, F2) instead of a document; backfill by deleting documents whose content starts with `[Error:`. Tick 15: one more (`EKH_DlasWo/Unterlagen/w_leuthner_2019-12-23-120011.pdf`), a second file of the same unreadable kind. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: reader placeholders raise `UnreadableFileError` and are recorded as failed; `quellexctl ingest purge-placeholders --profile diejuristin --apply` removes the 19 existing documents and their chunks); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. Tick 23: 7 unreadable files were recorded as `error` in `failed_documents` (e.g. `EKH_BlizMa/Unterlagen/w_leuthner_2020-11-25-105440.pdf`, "Could not read file") instead of being stored — fix verified. | 11, 15, 21, 25, 30, 36 |
| F27 | low | **The llama.cpp embedder returned HTTP 500 nine times within 8 minutes (21:57–22:05 UTC) with nothing in its own log.** The ingestion embedder client retried ("OpenAI server error (attempt 1/3), retrying in 1.0s") and no chunk in the window lacks an embedding (37,912 checked), so nothing was lost, but only 2 of the 9 errors show a retry line: the others hit the query-embedding path of search/chat (`backend/services/query_embedding.py`), which does not retry, and surfaced as failed searches. Fix: retry with backoff on the query path too, and count embedder 5xx in the host-stats widget. | 13, 22 |
| F28 | medium | **Long questions silently break the text stage of hybrid search.** Two chat requests on 26.10. 09:07 and 09:15 UTC (the loop's 40-word hand-crafted questions) logged `Text search failed for rag_diejuristin: Query contains too many nested clauses; maxClauseCount is set to 1024`: the `$search` text operator with `fuzzy: {maxEdits: 2, prefixLength: 3}` (`backend/agent/federated_search.py:312`, same in `backend/routers/search.py`) expands every term into dozens of variants, so a question with 25+ distinct terms exceeds Lucene's clause limit; the exception is caught as a warning and the answer is built from the vector stage alone, which is exactly when exact figures go missing. Fix: fuzzy only on terms of 5+ characters with `maxExpansions` capped, or extract the top-N keywords for the text stage, or retry the text stage without fuzzy on this error. **Fixed:** PR #34 (bounded fuzzy clauses, retry without fuzziness, 400 instead of 500), merged to main 2026-10-30. | 17, 21, 22 |
| F29 | medium | **The single GPU embedder is now the ceiling for ingestion and the shared bottleneck for search.** At tick 19 three `docker stats` samples showed the backend at 0.4–0.7 % CPU and the embedder at 100 %; in ten minutes 56 files finished converting and 56 "Generating embeddings" waits produced 62 embedding batches, i.e. with the text-heavy literature now in the queue every file converts instantly and then waits for its embeddings. Ingestion throughput therefore follows the embedder's batch rate (and has fallen from 31 to 13 files/min as documents grew longer), while every query embedding joins the same queue (F18). Fix: run a second embedder instance (the `embedder` CPU profile already exists; the ingestion pipeline could use it or a GPU instance with its own slots), or give query embeddings a reserved slot, and batch ingestion chunks per file instead of per 100. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: query embeddings retry with backoff and can use a second embedder (`QUERY_EMBEDDING_URL`, compose profile `embedder-query`, port 11091); the live embedder computes on the GPU (13-16k tok/s after a restart vs 606 tok/s degraded, see docs/04)); deployed to the live Quellex stack 2026-10-26 23:18 UTC (images `:findings-fix` = main `c475781`, rollback tags `rollback-20261027`), verified at tick 21. Tick 22 addendum: `docker stats` 99.7–100 % CPU on the embedder while six 1-s `nvidia-smi` samples showed 0 % GPU utilization (3.9 GB allocated); a 15-token request took 17–34 s three times in a row; the window's 4,274 sequences per 10 min at p50 287 tokens ≈ 2.0 k tok/s equal the chunk rate (5.8/s), so ingestion is embedder-bound and every query embedding queues behind it (F18). Vectors stay consistent (same sentence twice: cosine 0.9999). `embedder-query` / `QUERY_EMBEDDING_URL` of PR #29 is not in Verify's compose. Tick 23 (12 h later, no restart): 1.07 k tok/s (2,560 sequences in 10 min; ~14.5 k sequences per hour since 11:00 UTC), a 15-token request timed out at 120 s and then took 65 s, GPU 2–42 %; 13 query-embedding timeouts, 6 search request timeouts, 3 HTTP 504, every chat of the tick without sources or 504 — the embedder process degrades progressively (2.0 k tok/s at tick 22) and nothing but a restart recovers it. Tick 24: still no restart; 763 tok/s (1,645 sequences in 10 min), sequences per hour 12.6 k → 11.5 k → 9.9 k from 15:00 to 17:00 UTC, a 15-token request 90 s, GPU 0 % in four samples; 170 query-embedding retries, 29 search request timeouts and 34 HTTP 5xx in 3 h, all 24 probes and both scripted chats without retrieval. Add to the fix: a compose healthcheck on the embedder's own latency (a short `/v1/embeddings` call over a threshold → unhealthy) with automatic restart, since the degradation is progressive and only a restart recovers it. | 19, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F30 | medium | **1-bit CCITT fax scans render inverted and hollow, so their OCR returns nothing.** Of the first 160 `image_only_pdf` records after the deploy, 102 are `CCITTFaxDecode` PDFs, 77 of them from a Xerox WorkCentre 7132 (`CZ-1630/webErv/Geburtsurkunde Anna-Sophia.PDF`, 40 KB, 1 page). pypdfium2 renders the page with mean brightness 22 (white outline glyphs on black); Tesseract 5.5 reads nothing from the render, nor from the inverted render, nor from the native 2340x1654 bitmap. 43 other pure scans in the same window OCRed fine, so the OCR path works; the fax decoding/rendering does not. The other 58 are photos and JPEG-only pages with no readable text (expected). Fix: look at the CCITT `BlackIs1`/`Decode` handling of the page renderer (pdfium image extraction via PIL + libtiff, or Ghostscript rasterisation for `CCITTFaxDecode` pages) before OCR. | 21, 22 |
| F31 | low | **Two side effects of the process-isolated conversions.** (a) Every conversion worker process writes `/app/profiles.yaml` on startup (`load_settings()` -> `profile_manager.switch_profile(active_profile)` with `persist=True`, `src/settings.py:386`): 16 processes rewrote the file within one second at 23:29:40 UTC and two of them read an empty file in the race ("Loaded 0 profiles ... Profile 'diejuristin' not found ... using default"). Pass `persist=False` there, or skip the switch in worker processes. (b) `failed_documents.profile_key` is always `default`: it comes from `os.getenv("PROFILE", "default")` (`ingest.py:4412`), not from the job's profile. | 21 |
| F32 | medium | **Text with a surrogate-escaped legacy byte reaches the embedder as invalid UTF-8 and is rejected with HTTP 400.** The embedder log shows three identical rejections at 20:58 UTC (old image): `parse error ... ill-formed UTF-8 byte; last read: "Die Vermieterin vermietet und die Mieterin mietet den Mietgegenstand im Ausma<0xDF>"`, i.e. a cp1252 `ß` decoded with `surrogateescape` somewhere in a reader (legacy text/`.doc` path) and re-encoded raw by the HTTP client. No chunk with that sentence exists, so the file was lost without a record (F2 on the old image; the new image would record it as `error`). Fix: normalise reader output with `text.encode("utf-8", "replace").decode()` (or strip surrogates) before chunking, and treat a 400 from the embedder as a per-file error with the offending text logged. Related: F27. | 21 |
| F33 | medium | **Born-digital WebERV PDFs can come out with scrambled word order.** `Papierkorb/APMEA-ACT/Gericht/2022-07-01-Einspruch TP 3A-92769.pdf` (4 KB, text layer) is stored as "Mangelnde Die EUR Aktivund klagende 40.000,- Partei Klagebegehren Folgenden Zahlung der Kosten werden ausdrücklich Passivlegitimation hat gelegt." - the justified two-column body is read column-interleaved; the header table and the numbered points survive. The chat question about this Einspruch (ask 4 below) could not find "ACT Grundbau" and answered from the matter's Honorarnoten instead. Check Docling's reading order for this producer (WebERV uploads) and fall back to pypdfium2 text extraction when the Docling text has a low dictionary-word ratio. | 21 |
| F34 | high | **Hybrid RRF lets numeric-token text matches displace the semantic #1, so a statute asked for by name is not retrieved.** Hand-crafted question at tick 21: "Was regelt § 1 TKG 2021 (Zweck und Ziele) laut dem Gesetzestext in der Fassung vom 18.08.2022?". The statute itself (`FP-DIETR-MAGENTA/Recherche/TKG 2021, Fassung vom 18.08.2022.pdf`, ingested 2026-10-26 22:47 UTC) is semantic #1 and #2 for the question (its chunk starts "Zweck und Ziele - § 1. (1) Zweck dieses Bundesgesetzes ist es, durch Förderung des Wettbewerbes …", score 0.895) but is absent from the hybrid top 8 (RRF 0.031 … 0.016): the fuzzy text stage matches `1`, `2021` and `Ziel` in a COVID-19 guideline ("Ziel 1: Sicherung von Unternehmensstandorten"), two Syndikatsvertrag tables ("GJ 2021 = € 200.000") and an e-mail with a number column, and with equal RRF weights those displace the semantic hits. The agent (mode auto, 84 s) then answered from the Jahrbuch Datenschutzrecht 2021 and Zankl's Rechtshandbuch, opening with "Ja, die Regelung … ist im TKG 2021 enthalten" and listing the literature's goals as if they were § 1's content; mode fast (9 s) said the statute text is not in the documents. Sibling of F17 (Aktenzeichen): a statute citation `§ N <Gesetz> <Jahr>` is an identifier too. Fix (edge case only): detect statute citations the way PR #28 detects case numbers and match them as a phrase against title/section text; drop pure-number tokens from the fuzzy text stage; or guarantee the semantic top-3 a slot in the fused list. **Fixed:** PR #32 (statute citations as identifiers, with the review fixes), merged to main 2026-10-30. | 21 |
| F35 | high | **Docling drops the OCR text of scanned pages it classifies as one picture, so readable scans are recorded as "OCR found no text".** 484 of the first 539 pure scans after the deploy (90 %) ended as `image_only_pdf`. Of 12 random ones re-OCRed with plain Tesseract 5.5 (deu+eng) on a pypdfium2 render at 2×, 4 are readable — an e-mail printout (`Beilage I.PDF`, 55 words), a Sparkasse letter (`Direktzustellung_2009_03_24_4.pdf`, 209 words), a phone screenshot, a flyer — and 8 are empty (photos, IDs, a sketch; the CCITT class of F30). The pipeline's own converter (`create_document_converter()` run in the API container) on `HandMa_MileTh/Unterlagen/Aufklärungsbogen Aesthetic.Clinic vom 16.6.2017.pdf`: `ConversionStatus.SUCCESS`, items = {`PictureItem`: 1}, 1,568 characters of OCR text attached as the picture's children, `export_to_markdown()` = `<!-- image -->` → "No chunks created" → `image_only_pdf: OCR found no text`; same for `Beilage I.PDF` (736 characters inside the picture). `Direktzustellung_2009_03_24_4.pdf` is a second variant: Docling returns no items at all in 3.6 s although Tesseract reads the page. The `<!-- image -->`-only documents listed as "tiny" (`scan_20211109_120247.pdf`, `Beweiskonvolut Nachahmung ….pdf`, 165 chars) are the multi-page form of the same loss. These files are at least retryable now (F9). Fix (edge case only, no change for pages that yield text): after conversion, when a page produced no text items but a `PictureItem` with OCR children, export the children's text; when it produced nothing although the page is a full-page bitmap, run one full-page Tesseract pass on the pypdfium2 render. Tick 22 verification: on `akten/WambBe_EnglJa/Erlagschein.pdf` Docling's OCR produces 18 text items ("ZAHLUNGSANWEISUNG", "IBAN", "AT64", ...) that are children of the page's single `PictureItem`; `export_to_markdown()` keeps `<!-- image -->` (14 chars) while direct Tesseract on the rendered page reads 622 chars. The rollback image `recallhub-backend:rollback-20261027` and the plain `create_document_converter()` behave identically, so this is Docling 2.142 behaviour (in the images since the 2026-10-23 dependency refresh), not PR #29. Scale after 3.5 h: 1,271 of 2,988 files (43 %) are `image_only_pdf`; a 300-file sample is 76 % JPEG (`DCTDecode`: RICOH MP C3003, CamScanner, unknown producers) and 19 % CCITT (F30). Fix: re-parent the text children of pictures into the body in reading order before export/chunking. **Fixed:** PR #31 (scan-text fallback for picture-only pages, with the review fixes), merged to main 2026-10-30. | 21, 22, 23, 38 |
| F36 | medium | **The 1,024-token chunk budget does not reach the running job.** New-image chunks (after 23:19:30 UTC): 1,900, max 514 tokens, 918 of them ≥ 500. The resumed job keeps `max_tokens: 512` from its stored request (`ingestion_jobs.config`, submitted 2026-10-24) because `run_ingestion` prefers `config.get("max_tokens")` over the code default (`backend/routers/ingestion.py:1113`); the live container's environment and the Configuration page set nothing. The UI start request carries the chunk config (`chunk_size`, `chunk_overlap`, `max_tokens`, typed in `frontend/src/api/client.ts:3609`), which is how the 10-24 job got 512, so a new UI job would pin 512 again unless that value is updated. Fix: treat a stored `max_tokens` equal to the old default as unset, or store only values the user set explicitly; say in the UI which budget a resume keeps. | 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F37 | low | **Two counter gaps in the new live status.** (a) `image_only_pdfs_ocr` stays 0 while `ingestion_stats` holds 55 OCRed scans: `IngestionStatsRecorder.snapshot()` copies only the `COUNTED` keys and `image_only_ocr` is not one of them (`src/ingestion/ingest.py:2026`, `:2088`), so the mapping in `backend/services/ingestion_counters.py` never sees it. (b) `failed_files` (192 at 00:15 UTC) counts timeouts, errors and no-chunk files only; the 535 image-only files are a separate counter, so the UI's "failed" understates files without text by about 3×. Fix: add `image_only_ocr` to `COUNTED` (or snapshot every key) and show "files without text" = failed + image-only next to the ETA. | 21, 22 |
| F38 | medium | **Conversion worker processes grow to 4.6–5.4 GB RSS each and are never recycled.** 3.5 h after the deploy the 16 workers plus the API hold 64 GB RSS (`docker stats` 55 GB) of the 131 GB VM, up from 29 GB at 20 min; `MemAvailable` is 44 GB. The workers are long-lived (one per slot, restarted only after a kill) and Docling/Tesseract allocations accumulate per file. Add a files-per-worker (or RSS) limit after which a worker exits and the slot spawns a fresh one, like `maxtasksperchild`. Tick 25: backend container 58.1 GB (52.7 GB at tick 24, 54 GB at tick 23) with the same 16 conversion processes — the growth resumed with the large-PDF stretch. Tick 32: backend container at 75.9–76.6 GB RSS with 16 slots at 19:20 UTC (35 GB with 4–8 slots at 14:50) — the growth tracks the slot count × the size of the books in flight. Tick 35: 78.8 GB backend RSS with 16 slots (62.7 GB at tick 34). | 22, 23, 24, 25, 26, 32, 34, 35, 36, 38 |
| F39 | low | **Legacy MANZ text files are decoded with the wrong code page.** The sentence probe of this tick came from a document reading "Zweigniederlassungen inlŠndischer Unternehmer haben auf ihren GeschŠftspapieren ..." (`Unsortierte Dateien/ZZZZ_Dateien Katharina unsortiert/...`): Mac Roman `ä` (0x8A) read as cp1252 `Š`. The `.MTT`/legacy text reader should sniff Mac Roman (0x8A/0x9A/0x9F for ä/ö/ü, 0xA7 for ß) before falling back to cp1252. | 22, 36 |
| F40 | medium | **Chunks that the embedder's tokenizer counts above its context are retried three times per batch, then dropped.** 420 embedder HTTP 500 responses in this window, all `input (9439 tokens) is too large` style rejections of one chunk (e.g. chunk 491, 3,451 chars, of `FP-KIRCH-MV/Attachements/Hausversicherung KOP_2_...`): the chunker counted it under 514 tokens with its own tokenizer, EmbeddingGemma's SentencePiece counts a digit/pipe-heavy table at 9,439, above the 8,192-token slot. The client retries the whole 100-chunk batch three times (1 s, 2 s, 4 s), then drops the chunk (PR #24). Each such batch costs the embedder four passes. This is the root cause of F27. Fix: count with the embedding model's tokenizer (or bytes/4 as a bound) and split or drop before sending; retry only the remaining chunks. Tick 22 addendum: the 24 chunks dropped in the window (28 failed batches, 00:35–00:37 UTC) all belong to one broken-font PDF whose text is Hangul-range garbage (F41); EmbeddingGemma spends about 3 tokens per such character, so a 3,359-char chunk became 10,726 tokens. Tick 32: 5 batches rejected again with `input (N tokens) is too large to process` and 4 chunks of `P-76-11/Prospekte/Weitzer-Parkett-Preisliste-2012.pdf` dropped — a price list whose table rows tokenise far above the MiniLM count (F41 class). Tick 34: 13 chunks of `P-76-11/Prospekte/Weitzer-Parkett-Preisliste-2012.pdf` dropped after three retries each (`input (9579 tokens) is too large to process`, the price list's table rows tokenising far above their MiniLM count); the rest of the document was stored | 22, 32, 34 |
| F41 | medium | **A PDF with a broken font encoding is extracted as Hangul-range garbage and stored.** `documents/FP-KIRCH-MV/Attachements/Hausversicherung KOP_2_p_101_….pdf` (438 KB): 82 % of its text is runs of U+C000-block characters ("쀄쀇쀆쀎 …"), 506 chunks were embedded and stored (avg 86 MiniLM tokens, up to 2,978 chars) and 24 were dropped because the embedder counted them at 10,726 tokens (F40). The `EMBEDDING_MAX_TOKENS` guard counts MiniLM tokens, so it cannot see this. 1 of 929 documents since the restart, but the stored chunks pollute the index and surface for nothing. Fix: a script-ratio check in the PDF reader (non-Latin letters ≥ 50 % of a document in a German corpus → `UnreadableFileError` or the OCR fallback, since the page renders fine), and split instead of drop on the embedder's "too large". Related: F6, F32, F40. | 22, 32, 34, 35 |
| F42 | low | **The per-extension ETA extrapolates the PDF pace onto 215k Office and mail files.** `_build_pending_files_queue` sorts by type priority (txt/md, html, pdf, then docx/doc/wbk/msg, …) and size, so the run converts every PDF before the first `.doc`; after 3.5 h only `.pdf` has a window pace (10.4/min, down from 44 as the files grow) and the other 25 extensions fall back to the overall pace, which is the PDF pace: ETA 14.1 d at this tick, 5.2 d at tick 21, and it keeps rising until the 8,664 remaining PDFs are done. Fix: for extensions without a window pace use `DEFAULT_CONVERSION_SECONDS` from `backend/services/ingestion_estimator.py` (or the pre-ingestion estimate), and show the ETA of the current file type separately from the whole run. Tick 27: after the restart the `.pdf` window pace is back (4.2/min); ETA 32.2 d. Tick 39: the PDF stretch is nearly over and the ETA jumped from 43.5 d to 291 d — `.msg` got a window pace of 0.57/min from 15 successes among 70 attempts (85,748 files → 104 d) and `.wbk` a lifetime pace of 0.04/min (8,552 files → 147 d); a window pace needs a minimum sample (say 100 files or 30 min of that type) before it replaces the estimator's default, and Outlook `.msg` files should not be priced at the rate of their first OCR-heavy attachments. Tick 41: ETA 107.6 d (291 d two ticks ago): `.docx` got a real window pace (25/min, 18k left), `.msg` still 0.65/min from 179 files (85k left = 91 d of the 108), `.wbk` 0.74/min — the estimate now hangs on the `.msg` pace alone. | 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F43 | medium | **Table-heavy PDFs exceed the 1,800 s conversion budget and are retried with the same budget.** 18 files timed out in the window (Kassabuch reports `ER-Dach_KB_2019.PDF` and `ER_KB_2019-1.PDF`, `baugrundstueckspreise_2018.pdf`, `REVISLST.RPT20230105 103012.pdf`, Existenzminimum tables, …); successful conversions reach p99 1,379 s and the slowest 1,785 s (`AVZ ACC_19115.pdf`, 381 chunks; two Energieausweise 1,500 s) with `do_table_structure` on (TableFormer on CPU per page). The isolated workers were killed and replaced (F1 fix works), but the files are re-queued for a later round with the same budget ("Will retry with 1800s timeout", `src/ingestion/ingest.py:4680`), so each will burn up to 2 × 30 min more and most likely fail again; their stats appear only after the last attempt. Fix (edge case): retry a timed-out PDF once with table structure off or with a page-count-scaled budget, and record the first timeout in the stats at once. Tick 34: the 21:37 UTC build raised `timeout_config` to 3,600 s (× factor 4 at 16 night slots): the Kassabuch `ER-Dach_KB_2019.PDF` that had timed out at 1,800 s three times finished (395k chars), and conversions of up to 3,334 s were stored; 13 files still hit `3600s elapsed`. **Addressed:** PR #33 (retry budget lifted per attempt, tables off on the last attempt), merged to main 2026-10-30. | 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38 |
| F44 | high | **The conversion budget expires after a successful conversion while the document waits for the embedder, and the finished work is thrown away.** `_run_with_budget` (`src/ingestion/ingest.py:3800`) starts the 1,800 s budget at the conversion start but wraps the whole `_ingest_single_document` (conversion, chunking, embedding, storing). With the embedder at ~1 k tok/s (F29), a 300-page commentary converts in 17–20 min and then queues 50–100 k tokens of chunks: 46 of the 49 files that timed out between 02:50 and 14:52 UTC had logged "Successfully converted" before the TIMEOUT (`Weitzer-Parkett-Preisliste-2012.pdf`: converted 12:21:10 after 1,168 s, TIMEOUT 12:31:43; `Kommentar zum UWG, Görg, § 1 UWG.pdf`: converted 13:31:50, TIMEOUT 13:45:28); only 3 were still converting and got their worker killed. The timed-out document is not stored (0 of the 49 in `documents`, no partial chunk sets among the 3,584 new documents) and the file is re-queued for a later round with the same budget (F43), so about 46 × 20 min of conversion CPU plus the embedded chunks were discarded in 12 h, and under a slow embedder the retry fails the same way. Fix: stop the budget clock when the conversion returns (or give chunking + embedding their own, embedder-aware budget), keep the converted document when only the embedding phase runs late (embed the remaining chunks in a follow-up pass), and record the timeout stat on the first attempt. Tick 24: 30 of the 41 files that hit the budget in 3 h had already been converted (10 conversions killed); the loss runs at about ten converted documents per hour for as long as the embedder stays slow. Tick 25: 22 of the 40 budget timeouts in 3 h came after a successful conversion (18 conversions killed). Tick 26: 23 of 43 budget timeouts after a successful conversion (20 conversions killed). Tick 28 (the queue has reached the 800k–980k-character books): 48 of the 61 budget timeouts in 3 h came after a successful conversion, 39,252 s = 10.9 h of Docling conversion time discarded in the window (e.g. `Die Rechtsnatur der Auftraggeberhaftung …pdf`: converted at 03:32:52 after 8 min, TIMEOUT at 03:44:31 while its chunks queued for the embedder); only 8 documents were stored in the same 3 h (p50 1,085 s). At this point the budget, not the hardware, decides the pace: each book converts in 15–20 min and then its 400–900 chunks wait behind seven other books' chunks on an embedder at ~700 tok/s, so most of the stretch times out, is thrown away and is re-queued for a retry that will fail the same way. Tick 32: 18 of 67 budget timeouts after a successful conversion, 24,612 s = 6.8 h discarded in 4.5 h; 26 documents stored. Tick 33: 1 of 11 budget timeouts after a successful conversion (1,552 s discarded); with 8 slots feeding an idle embedder the budget hits the conversions themselves. Tick 34: 5 of 13 budget timeouts after a successful conversion, 14,263 s = 4.0 h discarded in 3 h; the longer budget does not change the mechanism. Tick 35: the worst window yet — 22 of 36 budget timeouts after a successful conversion, 66,815 s = 18.6 h of conversion discarded in 3 h against 13 documents stored: with 16 night slots the books convert in 45–60 min and then queue 700–1,500 chunks each behind fifteen others at an embedder running 470 tok/s, so even the 3,600 s budget expires during embedding. Tick 36: 19 of 38 budget timeouts after a successful conversion, 53,710 s = 14.9 h discarded in 3 h against 11 documents stored. Tick 37: 18 of 26 budget timeouts after a successful conversion, 46,179 s = 12.8 h discarded in 3 h — now with 8 day slots as well; PR #33 (open, not deployed) is the fix. Tick 39: 3 timeouts, all after a finished conversion (6,302 s discarded) in 8.6 h — the loss disappears with the case-file and Office stretch, the mechanism stays. **Fixed:** PR #33 (conversion budget scoped to the conversion, partial embedding with a resumable backlog, with the review fixes), merged to main 2026-10-30. | 23, 24, 25, 26, 27, 28, 29, 30, 32, 33, 34, 35, 36, 37, 38, 39 |
| F45 | low | **The agent's no-result fallback mixes English into a German answer.** Title summary "Rechnungen WITT" at tick 23 (retrieval timed out, 0 sources, 145 s): the German answer contains the block "**Wonach ich gesucht habe:** The user wants a summary of the document 'Rechnungen WITT', specifically identifying the subject matter, parties involved, dates, and monetary amounts." — the orchestrator's internal English query plan is pasted into the user-facing text (again in hand-crafted question 4: "The user is looking for specific medical and accident details …"). The fast path's refusal instead cites a placeholder as if it were a document: `[Source: "Keine relevanten Informationen gefunden"]` (question 5). Fix: render the "what I searched for" line from the user's own question (or in the answer language) in the fallback template, and never emit a `[Source: …]` tag without a retrieved document. | 23, 24, 25, 26, 27, 28, 35, 37, 38 |
| F46 | low | **Password-protected PDFs are reported as "Could not read file" instead of "encrypted".** The 7 `error` records of this run (`akten/EKH_SpitRo/Korrespondenz/w_leuthner_2021-10-25-104117.pdf` and five more `w_leuthner_*.pdf`, `Herr Günter Lepuschitz_2019S5549.pdf`, 1.3–2.1 MB each) carry `/Encrypt` with the Standard security handler; pypdfium2 fails with "Incorrect password error", Docling likewise, and the reader's message is the generic placeholder text. The 42 `[Error:` placeholder documents still in the database (F26) are the same file names from the old image. Fix: detect `/Encrypt` (or catch the password error) in the PDF reader and record `error_type: encrypted` with its own message, so the status page can list them for the firm (they need the owner's password, not a retry). | 23, 25 |
| F47 | medium | **The llama.cpp embedder's throughput decays over hours of continuous ingestion and only a restart restores it.** Aggregate rate measured from its own log (10-minute samples): 2.0 k tok/s at tick 22 (02:50 UTC, 4.5 h after its restart), 1.07 k at tick 23, 729–763 tok/s at tick 24; RSS 4.6 GB fresh → 6.3 GB (tick 21) → 9.5 GB (tick 22) → 9.75 GB (tick 24); `nvidia-smi` 0–28 % while one CPU core sits at 100 %; a 10-token request takes 100–105 s. The same pattern was measured on 2026-10-26 (606 tok/s, 10.4 GB after two days; `docker restart rag-embedder-gpu` gave 13–16 k tok/s, docs/04). Startup flags: `-c 32768 -np 4 -b 8192 -ub 8192 -ngl 999`. Fix: find the growing structure (prompt/slot cache — check `--cache-ram`/`--cache-reuse` of this build, or the batch of 100 inputs per request) and, until then, restart the embedder on a schedule or when its rate drops below a threshold; the ingestion client should also send fewer inputs per request so a restart costs one small batch. Tick 25 (22.3 h since the embedder's restart): 956 tok/s in the 10-minute sample, 11.0 k / 11.1 k / 9.1 k sequences per hour from 18:00 to 20:00 UTC, a 15-token request over the 60 s client timeout, GPU 0 %; 217 query-embedding retries and 54 HTTP 5xx on search in 3 h. Tick 26 (25.3 h since the embedder's restart): 720 tok/s, hourly sequences 10.4 k / 9.9 k / 8.1 k (21:00–23:00 UTC), a 15-token request over the 60 s client timeout; 106 query-embedding retries, 16 search request timeouts, 22 HTTP 5xx and 3 chat 504s in 3 h. Tick 27: without any embedder restart, throughput rose from 720 to 1,420 tok/s (hourly sequences 10 k → 17.7 k) within two hours of the backend's conversion concurrency being halved from 16 to 8 at 00:30 UTC (host CPU 100 % → 74 %). Hypothesis to test next: the decay is at least partly CPU contention — llama-server's single busy thread competes with 16 conversion processes on the same cores — rather than only internal degradation; a `cpuset`/priority for the embedder container, or fewer conversion slots, would then help as much as a restart. A 15-token request still exceeds the 60 s client timeout (queue depth). Tick 28: the relief lasted three hours — 15.9 k sequences in the 03:00 hour, 9.7 k at 04:00, 8.2 k at 05:00, 678 tok/s in the 10-minute sample at 05:50, GPU 0 %, a 15-token request 35 s. The decay therefore returns under the big-book load even with 8 conversion slots and a host at ~75 % CPU; CPU contention explains the step at 00:30, not the slide since. | 24, 25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44 |
| F48 | medium | **The identifier path does not normalise the ERV spelling of case numbers (leading zeros, upper case).** ERV form PDFs print the court and case number as "003 - Landesgericht für ZRS Wien" / "062 CG 27/2020a" (`MühlbMa_1AndrDa/Gericht/Mühlbauer 1 VSS.pdf`, ingested 18:06 UTC on 2026-10-27); asked for "62 Cg 27/2020a" (the spelling a lawyer uses) the identifier path of PR #28 found nothing, hybrid search returned MANZ form texts and unrelated filings, and the agent honestly reported three other case numbers instead (tick 25, question 7). Fix: in `identifier_search.py` strip leading zeros from the Gerichtsabteilung and compare the register letters case-insensitively (`062 CG` ≡ `62 Cg`), and index the ERV header's "GZ"/"Aktenzeichen" lines in the normalised form; related: F17. Tick 27, spacing variant: the court expert's Gutachten in `MaieRi_MaurHe/Gutachten/Gutachten.pdf` prints the number as "65 Cg1/19y-19" (no space before the number), so the question for 65 Cg 1/19y retrieved ten other documents of the matter but not the Gutachten that holds the answer; normalise whitespace between the register letters and the number as well. Tick 27: a Grundbuch Tagebuchzahl ("TZ 5424/2026", Bezirksgericht Döbling, Verbesserungsauftrag of 27.10.2026) is not recognised as an identifier at all (no "Identifier query" log line), so the question fell back to plain hybrid search and got commentaries about Verbesserungsaufträge; add the `TZ n/yyyy` pattern. Tick 30, OCR variant: the Ristic Klage prints its number as "7 Cg 110121d" (the slash lost in OCR), so the identifier path could not pin it and the fast path answered the question about 7 Cg 110/21d from another matter (F20). | 25, 27, 30 |
| F49 | medium | **The API-run job pinned the host at 100 % CPU / 90 W with the hottest core at 92–94 °C for four days, and nothing in the stack throttles it.** Host telemetry (host_samples) shows the CPU at ~100 % and 90 W with cores at 92–94 °C since 2026-10-24, caused by job b98d150e converting 16 files at once inside the API process (F7); the client reported the mini PC as loud and hot. On 2026-10-28 00:30 UTC an operator set `performance_config` to max_concurrent_files 8 / thread_pool_workers 8 (from 16/16) via `POST /api/v1/system/ingestion-performance` and restarted the backend so the auto-resumed job would read it (74 % CPU / 73 W afterwards): the change needs a restart, and the restart costs a full re-discovery (F23). Fix: honour performance-config changes at runtime (resize the slot limiter between files), add a thermal/power-aware ceiling (host temperature or power from the telemetry samples → fewer slots), and keep a load profile (server room vs office hours) in the config. **Fix deployed 2026-10-28 11:54 UTC** (images `:load-profiles`, commit `0ce9001`, branch `feature/ingestion-load-profiles`, not yet on `main`): load profiles with office-hours limits (`office_lite`: 4 files 08:00–18:00 Vienna weekdays, 16 otherwise), applied live. Tick 32: the thermal problem was also attacked at the OS level — at 16:47 UTC the user capped the Windows power plan (max processor state 99 %, boost disabled; `scripts/Set-CpuBoostCap.ps1`): 4.5 → 2.24 GHz, 87 → 23–28 W, hottest core 92 → 58 °C, indexing pace unchanged at ~100 pages/min with 8 slots; `office_lite` (4 slots) was abandoned as too slow (F51), the live profile is `office` (8 by day, 16 at night) and the night switch to 16 slots fired at 18:00:40 UTC without a restart. **Addressed:** PR #30 (load profiles office / office_lite, CPU clock cap through the host agent), merged to main 2026-10-30. | 27, 30, 31, 32, 33, 34, 37 |
| F50 | low | **A failed agent sub-task is reported as a "circular dependency" and its dependents are silently dropped.** `backend/agent/worker_pool.py` schedules tasks whose `depends_on` are all in `completed`; a task that failed (here `task_N execution failed: Request timed out`, the search timeouts of F18) never enters `completed`, so on the next round nothing is ready, the pool logs `[req=f083d2f1] Circular dependency detected in tasks: ['task_3']` at ERROR and breaks out of the loop (2026-10-28 06:04:20 UTC, during the fork's RS0021755 question, which the agent still answered from the anchor search). Once since the deploy. Fix: treat a failed task as resolved for scheduling (mark it completed-with-error so dependents run or are skipped with their own log line), and reserve the "circular" message for a real cycle detected before execution. Tick 30: a second occurrence (`[req=bfcea…] Circular dependency detected in tasks: ['task_N']`). | 29, 30 |
| F51 | medium | **The load profile shrinks the per-file budget, so the table-heavy PDFs are now killed after ~750 s instead of 1,800 s and can never finish.** `conversion_budget_factor` multiplies the base budget by the CPU share one conversion gets (factor 4 at 16 slots, 2 at 8, 1 at 4), assuming a conversion speeds up with more threads. Under `office_lite` (4 slots, 12:03–14:20 UTC) the 23 timeouts of the window all fired at 731–754 s: the same Kassabuch/REVISLST/IVBEWERT/Existenzminimum files that needed more than 1,800 s at 16 slots (F43) — TableFormer per page does not scale with threads, and the resume reset their attempt counters, so each restart re-runs them from attempt 1/3 (F23 skips recorded failures only; timeouts are recorded after the third attempt). Fix: keep the absolute cap as the floor for the budget (never below the 16-slot budget), count attempts across resumes, and after the first timeout send a file to a single-slot "slow lane" with `do_table_structure` off. Tick 32: all 67 timeouts of the window fired at `1800s elapsed` once the slots were back at 8–16 (budget factor 2–4); the ~750 s budget of the 4-slot period was not seen again. | 31, 32, 34 |
| F52 | low | **After the 19:21 UTC restart the resumed job runs with a ceiling of 8 slots outside office hours although the saved performance config says 16.** Before the recreate the night switch logged `File slots: 16 of 16 (outside office hours, load profile office), was 8` at 18:00:40; the config save at 19:19:24 logged `concurrent_files=16 … 8 files at once` (office hours); after the resume the limiter logged `File slots: 8 of 8 (outside office hours, load profile office)` at 19:32:13 and the status reports `concurrency_slots: 8`, `load_period: off_hours`. The ceiling (`max_concurrent` in `run_ingestion`, read from the performance config at job start, `backend/routers/ingestion.py:1269` on the branch) therefore came out as 8 — either from a config document other than the one the save endpoint writes, or from a save without a log line between 19:19 and 19:32. Effect: the night runs at half the intended rate (4 documents in 1.5 h). Fix: log the config document the resume reads, and show the ceiling's source in the status. | 33 |
| F53 | high | **With 16 night slots the Docker VM runs out of memory and the kernel OOM-kills a conversion worker.** At 03:29:18 UTC on 2026-10-29 conversion worker pid 13195 (slot 2, alive since 23:57, converting `documents/ERZBI-ISTA/Attachements/Einzelabrechnungen 2023.pdf`) vanished: `conversion worker 13195 exited (code None) before answering`, the file was recorded as `error`, and the backend container's cgroup shows `oom_kill 1` with `OOMKilled: true` — no container memory limit (`memory.max = max`), so the 125.6 GiB WSL VM itself was exhausted: backend 79 GB (16 workers of 4–7 GB plus the API), MongoDB 22 GB, embedder 10 GB, 6 GB swap. The OOM killer picks the largest process; a slightly different moment and it is the API process (job lost, chat down) or MongoDB. Fix: cap the night profile where 16 × worker RSS + MongoDB + embedder stays under ~100 GB (12 slots on this box), recycle workers above a size (F38), and put a memory limit on the backend container so an OOM stays inside it. Tick 37: no further OOM kill (cgroup `oom_kill 1`), MemAvailable 45 GB at 08:50 with 8 day slots and the backend at 52.6 GB. | 36, 37, 38, 39, 41, 42, 43 |
| F54 | low | **Word owner/lock files (`~$name.doc`, 162 bytes) are queued, fail one by one with a full traceback, and the real failures hide behind them.** 575 of the 1,049 `error` records written between 11:51 and 20:27 UTC are `~$…doc` lock files (`Skipped: ~$norarnote_N-N.doc - legacy .doc format requires antiword`), each logged at ERROR with a traceback (966 tracebacks in the window); the other 474 are genuine `.DOC` files antiword could not read (`ROSENKN.DOC`, `SIEN.DOC`, `JERRY_N.DOC` …, old Word 2/6 or non-Word binaries) with the same misleading message although antiword is installed and converted 7,151 others. Fix: drop `~$*` and other Office temp patterns in discovery (next to the `._` AppleDouble skip), log reader skips at WARNING without a traceback, and say "antiword could not parse" when antiword exists. Fork check on `texte/Czinglar Archiv/RAK/1365_1.DOC` (7,680 bytes, OLE2 magic d0cf11e0): `antiword` answers "I'm afraid the text stream of this file is too small to handle" — the 474 genuine failures are mostly 2.5–7.7 KB Word files whose text stream is empty or a few words (fast-saved stubs, templates); the reader turns every antiword non-zero exit into "requires antiword". Fix: pass antiword's message through, and fall back to `olefile` (read the `WordDocument` stream's text) or LibreOffice for the ones that do hold text. Tick 42: 3 more `requires antiword` skips in the window (`ERV-Exekution-1.doc`, `Scheidung1.doc`, an `Abänderungs_und Verlängerungsvereinbarung`), no lock files. Tick 43: 14 `requires antiword` errors, none a lock file — 11 are valid Word 97 files whose text is empty (`fcMac - fcMin` = 2 characters, 22,528 bytes each: eight `Grundbuch - <Datum>.doc` / `GBAuszug KG … EZ ….doc` exports and the `Briefkopf-Pittner.doc` letterhead; antiword exits 0 with 3 bytes of output), so the reader reports a missing antiword when the document is simply empty — those belong in `no_chunks`, and the Grundbuch names should be kept as title-only hits for the EZ/KG identifier path. | 39, 40, 41, 42, 43, 44 |
| F55 | medium | **Legacy `.doc` and `.rtf` text bypasses the HybridChunker: every such file is cut by the simple fallback.** `src.ingestion.chunker - WARNING - No DoclingDocument provided, using simple chunking fallback` 7,841 times in the window; 25,569 of the window's 54,083 chunks carry `chunk_method: simple_fallback` (avg 226 tokens, 779 under 20 tokens) against 28,514 `hybrid` (avg 331). The antiword/striprtf readers return plain text, so headings, letter structure and tables are not used for chunk boundaries and the contextualised chunk text of the hybrid path is missing — 112k `.doc`, 6k `.rtf` and 86k `.msg` files are still to come this way. Fix: wrap the plain-text readers' output in a minimal `DoclingDocument` (paragraph items split on blank lines, headings from ALL-CAPS/`Betrifft:` lines) so the HybridChunker and its context prefix apply, or at least a sentence-aware splitter with a 20-token minimum. Tick 41: 11,148 of the window's 25,124 chunks are `simple_fallback` (avg 271 tokens, max 939 — almost twice the 512 budget — 181 under 20); 2,630 fallback warnings in 3 h. Tick 42: the `.doc` stretch — 19,583 of the window's 25,105 chunks (78 %) are `simple_fallback` (avg 221 tokens, max 711, 667 under 20 tokens); 7,163 fallback warnings in 3 h. | 39, 40, 41, 42, 43, 44 |
| F56 | medium | **A long query breaks the text and hybrid search with Lucene's `maxClauseCount is set to 1024` — HTTP 500 instead of a degraded result.** First seen 2026-10-30 02:51 UTC: two sentence probes of the fork's tick 42 with 26 and 28 distinct terms got HTTP 500 from both `/api/v1/search/text` and `/api/v1/search/hybrid`, probes with up to 23 terms passed. `backend/routers/search.py` sends every query term with `fuzzy: {maxEdits: 2}` (the text endpoint, l.130, without `prefixLength`; hybrid l.267 and the federated search with `prefixLength: 3`) and Atlas Search expands each fuzzy term into up to 50 variants (`maxExpansions` default), so a query of more than ~20 distinct terms exceeds the 1,024-clause limit of the Lucene BooleanQuery and the whole request fails. Pasted paragraphs and the agent's long rewritten queries are exactly this shape. Fix: cap the fuzzy clause (`maxExpansions` 10–20, `prefixLength` 2–3 everywhere, fuzzy only for the first N terms, or no fuzzy on queries longer than 15 terms), and on this error retry once without fuzzy instead of returning 500. Tick 44: 4 more occurrences in the window (2 text, 2 hybrid, 11 log lines) — every probe round of the fork session now trips it. **Fixed:** PR #34 (bounded fuzzy clauses, retry without fuzziness, 400 instead of 500), merged to main 2026-10-30. | 42, 43, 44 |
| F57 | low | **`.docx` files that are not OOXML, or whose package is incomplete, fail as "Could not read file" with the reason hidden.** 5 of the window's 1,410 `.docx`: `Prov Kurse.docx` starts with `D0 CF 11 E0` — an OLE2 legacy `.doc` renamed to `.docx` (python-docx cannot open it, antiword could); the three jurXpert time-sheet exports `Stundensatz, Abrechnung … .docx` / `Klient Einheitssatz, Abrechnung … .docx` are valid zips with `word/document.xml` but no `officeDocument` relationship in the package (`KeyError: no relationship of type …/officeDocument`), and `2.3.1 Umwandlung-einer-GmbH-in-eine-OG-1079845.docx`. The log and `failed_documents` show only `Error: Could not read file <name>`. Fix: sniff the magic bytes before the extension (OLE2 → the `.doc` reader, as the `.wvb`/`.mtt` sniffing does), fall back to reading `word/document.xml` directly when python-docx raises on the package relationships, and keep the underlying exception text in the error message. Tick 43: the mismatch runs both ways — `ws1417873000.DOC` is a zip (`PK`, a `.docx` saved as `.DOC`) and `ERV-Mahnklage.doc` is RTF (`{\\rtf1`), both rejected as "requires antiword"; sniffing the first bytes before choosing the reader fixes all four cases. | 42, 43, 44 |
| F58 | low | **Word backup copies (`Sicherungskopie von <name>.wbk`) are indexed as documents of their own.** 1,629 `.wbk` in `documents` (1,581 of them in this window, 8,562 still queued): Word writes them next to the `.doc` as the previous saved version, so each is a near-duplicate draft of its neighbour (49 of 400 sampled have a same-stem `.doc` already indexed, none with an identical `content_hash`, so the hash dedup of F11 never collapses them) and the retrieval window fills with two versions of the same Aktenvermerk under two titles. Fix: skip `.wbk` by default (or only when the same-stem `.doc`/`.docx` exists), or ingest them tagged as `version_of` the sibling and hide them from the default search. | 42, 44 |
| F59 | low | **`.msg` subjects are stored with a trailing NUL byte.** 239 of the 571 `.msg` documents indexed so far have a title ending in `\x00` (`'RB Privatstiftung\x00'`, `'RE: CL-Conference 2007/Vienna\x00'`; the fork's tick 43 answer listed `[Source: "Piltz\x00"]` and `"Pachtvertrag Prinz\x00"`), i.e. the MAPI subject string's terminator is kept by the `.msg` reader; `.doc`/`.docx`/`.pdf` titles are clean (0 of 3,000). Effects: ugly citations, titles that no regex or `$search` phrase can match (MongoDB refuses a regex with an embedded NUL), and the "ignored null byte" noise when the backend log is grepped. Fix: strip `\x00` (and other C0 controls) from subject, sender and body in the `.msg`/`.eml` reader and in `metadata.title` generally; a one-off `$replaceAll`-style backfill fixes the 239. | 43 |

## Tick log

### Tick 1 — 2026-10-24 11:02 UTC (window 08:55 → 11:02, 2.1 h)

Job: running, 1,179 of 361,819 files (0.33 %), 10.2 files/min, ETA 26.8 days.
Database: +319 documents (302 pdf, 17 htm), +6,262 chunks, totals 39,633 /
233,876. No zero-chunk documents, no re-ingestion in place. Duplicate content:
36 groups / 65 redundant documents. Content size: 1 document under 200 chars
(`SK-259/webErv/GB-Auszug.pdf`, 73 chars, a table header only), 21 over 100 k
chars.

Logs (backend, 25,213 lines): 209 timeouts (168 distinct files), 471 no-chunk
files, 235 OSD failures, 222 Docling table-cell warnings, 17 `bs4.dammit`
decode warnings, 7 health-check request timeouts, 1 HTTP 504, 8 rate-limit
hits. Worker: idle (8 lines).

Retrieval probes (12): hybrid 6 / 10 / 10 at ranks 1 / 5 / 10, median 988 ms;
semantic 5 / 5 / 7, median 163 ms. Misses: two Firmenbuch extracts probed with
shared boilerplate ("Grundlage dieses Auszuges ist das Hauptbuch …").

Chat (agent mode auto, gemma4:12b, language de):

1. Control "Welche Unterlagen gibt es zur ALPO CONSULT GmbH (FN 404945h) …" —
   51 s, correct (Unternehmensberatung, Buchhaltung und Handel; cites the
   Firmenbuchabfrage), 10 sources, but 2 of the 10 are copies of the same file
   (`Gelöscht/Hy-Powe_Lubcon/…` and `Hy-Powe_Lubcon/…`).
2. Sentence lookup ("Die mit 06.09.2018 historischen Daten sind durchgestrichen
   …") — 47 s, correct document (`P-18-18/FBAuszug FBN 259167d_2018-09-06`),
   good summary of the extract.
3. Title summary ("Grundbuchabfrage, KG 11029, EZ 52") — 54 s, **not found**
   although 3 copies exist (F5). No hallucination: the agent said so and
   listed what it did find for KG 11029.

Observations recorded as F1–F13. Script fixes made after this tick: the OCR
heuristic now strips markdown table/image noise before scoring (262 of 300
sampled documents had been flagged on table pipes alone), probes skip
boilerplate sentences, and the tick records a `docker stats` snapshot.

### Tick 2 — 2026-10-24 13:50 UTC (window 11:02 → 13:50, 2.8 h)

Job: running, 5,543 of 361,819 files (1.53 %), 19.5 files/min, ETA 13.1
days; +4,364 files and +4,212 documents (job counter) since tick 1.
Database: +4,231 documents (all pdf), +32,795 chunks, totals 43,865 /
266,675. Inserts per 30 min from 11:00: 143, 115, 90, 878, 1,784, 1,474, the
recovery described under F1. 15 documents under 200 chars (image placeholders),
duplicate content 477 groups / 710 redundant documents (17 %).
Containers: backend 1,514 % CPU and 30.7 GB RSS, embedder 98 % CPU (busy
embedding), MongoDB 6.7 GB, worker idle.

Logs (backend, 98,613 lines): 0 timeouts, 33 no-chunk files, 3,796 OSD
failures, 81 table-cell warnings, 1 invalid PDF
(`…/FPLP/carolinef/RAM/CAF_FADaniloZEC/Wasserkaraffe/RechnungLutz.pdf`,
docling-parse could not load it; the raw-text fallback with simple chunking
handled it), 18 HTTP 429 on the search API (F14).

Retrieval probes (12): 6 lost to the 429s; of the other 6, hybrid 3 / 5 / 6 at
ranks 1 / 5 / 10 (median 414 ms), semantic 3 / 3 / 3 (median 618 ms, up from
163 ms at tick 1 while the embedder is saturated). 12 boilerplate candidates
skipped.

Chat, scripted (agent mode auto, gemma4:12b, language de):

1. Control ALPO CONSULT — 63 s, correct, as in tick 1.
2. Sentence lookup (`LinsDo_mjKili/Gericht/Sonstige Erledigung, Beschluss-3-1.pdf`)
   — 49 s, correct document, quotes the passage.
3. Title summary "Sonstige Erledigung, Kostenbestimmung für
   Drittschuldnererklärung" — 69 s, fluent and plausible, but it summarised a
   different case's document (`StahRu_Darlehen/…`) than the window's
   (`DohnEl_Todic/…`); 30 documents share the title (F15).

Chat, hand-crafted:

4. "Was wurde in der Hauptverhandlung am 15.03.2016 vor dem Bezirksgericht
   Innere Stadt Wien in der Strafsache 13 U 39/16v beschlossen, und wer war der
   Privatbeteiligtenvertreter?" — 59 s, **wrong**. The protocol
   (`CZ-1835/webErv/SonstigeErledigung_16092_75279_2016_04_13.pdf`: Verhandlung
   in Abwesenheit, Beweisverfahren eröffnet, Privatbeteiligter schließt sich
   mit € 2.512,22 an, vertagt auf unbestimmte Zeit; Vertreter Dr. Michael
   Czinglar) was not retrieved. The answer cited the sibling cover letter and
   padded with cost entries dated 15.03.2016 from an unrelated case (F17).
5. "Welche Dokumente mit dem Titel 'Sonstige Erledigung, Kostenbestimmung für
   Drittschuldnererklärung' gibt es, und zu welchen Akten gehören sie?" — 53 s,
   good: lists about 14 distinct Drittschuldner cases with the usual
   € 15–35 amounts and § 302 EO, says there are several, but attributes them to
   document titles rather than case folders (F15).
6. Negative control "Kaution im Mietvertrag der Firma Glassresq für
   Faschinggasse 3 in Retz" (entities from two unrelated documents) — 51 s,
   correctly says the documents do not contain it and invents no amount; it
   then lists Kaution amounts of three unrelated contracts, which a skimming
   reader could mistake for the answer.

New findings F14–F17; recurring F1 (drained by itself for this batch), F4, F7,
F8, F9, F12, F13. Script change: wait and retry once on HTTP 429.

### Tick 3 — 2026-10-24 16:50 UTC (window 13:50 → 16:50, 3.0 h)

Job: running, 14,055 of 361,819 files (3.88 %), 30.3 files/min, ETA 8.1 days;
+8,512 files since tick 2. Database: +8,400 documents (all pdf), +74,283
chunks, totals 52,264 / 340,954; 40 documents under 200 chars; duplicate
content 1,268 groups / 1,845 redundant documents (22 %). Containers: backend
1,105 % CPU and 32.5 GB RSS, embedder 100 % busy, MongoDB 7.8 GB, worker idle.

Logs (backend, 106,128 lines): 1 timeout
(`ERZBI-ISTA/Attachements/31.05.2024(AP14564589).PDF`, 905 s), 53 no-chunk
files, 4,553 OSD failures, 55 table-cell warnings, no 429s.

Retrieval probes (12): hybrid 4 / 8 / 10 at ranks 1 / 5 / 10, semantic
4 / 5 / 5, 7 boilerplate candidates skipped. **Search latency: hybrid median
7.3 s, semantic 9.0 s** (0.4 / 0.6 s at tick 2), text search 47 ms, because the
query embedding waits behind the ingestion batches on the embedder (F18).
Misses: a Zahlungsbefehl probed with a standard phrase (rank 10), a company
address line, a lawyer's signature line.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 68 s, correct, but the Firmenbuchabfrage is no longer
   among the first five sources; unrelated files
   (`Pittner Archiv/SS-94/13-10ACM.ASS`, ZPO literature) rank above it.
2. Sentence lookup (`CZ-1830/webErv/Firmenbuch_SonstigerBeschluss_13909_63647_2015_02_18.pdf`)
   — 64 s, correct document and content (Icebear Entfeuchtung & Klima GmbH,
   FN 62355 z, Prokura).
3. Title summary "Sonstige Erledigung, Beschluss-1-3" — 60 s; the agent said it
   did not find that title and summarised "Sonstige Erledigung, Protokoll, 2-3"
   as the closest match (F5, F15). Transparent, but still a summary of the
   wrong document.

Chat, hand-crafted:

4. "Welche Honorarnote wurde im Akt P-46A/16 … am 16.10.2020 gelegt, an wen ist
   sie adressiert und welchen Leistungszeitraum deckt sie ab?" — 49 s, mostly
   right (both Honorarnoten P-81/20 and P-82/20, their periods, Aktenzahl,
   Gegner, Schaden-Nr.), **one error**: the addressee is given as Sila
   Schweißtechnik GmbH, which is the "Ihr VN" line; the note is addressed to
   D.A.S. Rechtsschutz AG (F20). All 10 sources are copies of the same two
   files (F4).
5. "Welches KSV1870-Rating und welche Ausfallswahrscheinlichkeit hat die
   Schilling Werbe GmbH laut der KSV-Auskunft vom 20.10.2021?" — auto mode,
   61 s, correct (301, 0,20 %, KSV number, FN, status); 10 sources, 2 distinct
   files.
6. The same question in **fast** mode — 15 s, correct, but it adds a "Hinweis"
   that another document shows rating 411 / 1,59 %; that document is the KSV
   profile of Agrar-Brücke GmbH, a different company (F20). Fast mode reports
   0 tokens (F11).

New findings F18–F20; recurring F1, F4, F5, F6, F7, F8, F9, F11, F12, F13,
F15, F16.

### Tick 4 — 2026-10-24 19:50 UTC (window 16:50 → 19:50, 3.0 h)

The scripted tick took 11 minutes instead of 6 because of the search latency
below. Job: running, 20,035 of 361,819 files (5.54 %), 31 files/min, ETA 7.8
days; +5,980 files since tick 3. Database: +5,878 documents (all pdf), +63,808
chunks, totals 58,142 / 404,762; 61 documents under 200 chars; duplicate
content 904 groups / 1,278 redundant documents (22 %). Containers: backend
505 % CPU (the WEKA Gewerbeordnung literature needs little OCR) and 34.0 GB
RSS, embedder 100 % busy, MongoDB 8.2 GB, worker idle.

Logs (backend, 109,016 lines): 1 timeout
(`ST-2-11/Attachements/PDF-Dokument Beilage(1).pdf`, 906 s), 69 no-chunk
files, 5,406 OSD failures, 86 table-cell warnings, 58 "Dropping hyperlink
with unsafe scheme" (Docling serializer, harmless), 1 traceback (F21), 1
invalid PDF (an OGH Volltext). 9 search requests ended in HTTP 504 (F18).

Retrieval probes (12): hybrid 4 / 7 / 8 at ranks 1 / 5 / 10 of 9 answered
(median 16.7 s, 3 × 504); semantic 2 / 2 / 2 of 6 (median 23.8 s, 6 × 504).

Chat, scripted (agent mode auto, 83–95 s each):

1. Control ALPO CONSULT — 92 s, correct.
2. Sentence lookup (`P-103E-09/webErv/SonstigeErledigung_13383_61209_2014_10_30.pdf`)
   — 95 s, correct document, good summary of the § 212a ZPO protocol.
3. Title summary "SonstigeErledigung_19058_91561_2018_03_29" — 83 s, not found
   (F5); honest refusal.

Chat, hand-crafted:

4. "Wie hat das Landesgericht für ZRS Wien in der Rechtssache 17 Cg 114/11a
   (Badawi gegen Feil, Operation vom 14.12.2010) entschieden, und welche
   Prozesskosten muss der Kläger ersetzen?" — auto 103 s (2 iterations), all 10
   sources from the right case folder, dismissal and costs correct
   (EUR 8.092,30 incl. EUR 1.347,25 USt and EUR 16 Barauslagen, 14 Tage), but
   the stated reason "mangels passiver Klagslegitimation, § 52d Abs 6 ÄrzteG"
   is **not in the judgment**; it comes from the same-titled judgment of the
   unrelated case `Zürich_AnsaEv/` (F20, F15).
5. "Welche Pauschalversicherungssumme und welche Selbstbeteiligung sieht die
   Vermögensschadenhaftpflicht-Polizze der Prafa GmbH … vor, und wer ist der
   Versicherer?" — 85 s, sums and deductible correct
   (EUR 2.000.000; 10 %, min 2.180, max 10.900), insurer wrongly declared
   absent (F22).
6. Question 4 in **fast** mode — 22 s, same correct facts, same invented
   reason, ranked the two same-titled judgments first and second (F11, F20).

New findings F21, F22; F20 raised to high; recurring F1, F4, F5, F7, F8, F9,
F11, F12, F13, F15, F16, F18.

### Tick 5 — 2026-10-24 22:53 UTC (window 19:50 → 22:53, 3.05 h)

At 22:56:54 UTC, three minutes into the tick, the backend, worker and
frontend were recreated from new images (`recallhub-backend:db-stats`, main
`fd8211b`; previous images tagged `rollback-20261025`). 10 of 12 probes and
all three scripted chat prompts failed with "server disconnected" or 504
(F23); the three chats were re-run by hand afterwards, while the resumed job
was still in its discovery phase, so the latencies below are "idle API"
numbers.

Before the redeploy: job at 24,933 of 361,819 files (6.89 %), 30 files/min,
ETA 7.9 days; +4,824 documents, +56,441 chunks in the window, totals
62,966 / 461,203; duplicate content 890 groups / 1,233 redundant (26 %);
42 documents under 200 chars. Logs (136,502 lines): 0 timeouts, 56 no-chunk
files, 6,315 OSD failures, 112 table-cell warnings. After the redeploy: job
resumed as "discovering", counters 0 / `null`.

Retrieval probes: 2 answered (hybrid rank 1 and miss; semantic ranks 1 and 5).

Chat (all run after the restart):

1. Control ALPO CONSULT — 43 s, correct, Firmenbuchabfrage ranked first.
2. "Worum geht es in dem GVÖ-Tagungsbeitrag zur OGH-Entscheidung 2 Ob 225/07p,
   und wie ist der Beitrag gegliedert?" — 50 s, correct topic and the six
   section headings; the same paper is ingested twice under different names
   (`dokument(5).pdf` and `Die GmbH & Co KG nach …pdf`, F4), and one paragraph
   about liquidation comes from a sibling paper, cited as such.
3. "Welcher Streitwert und welche Kosten wurden bei der Klagsrückziehung im
   Verfahren 038 C 1199/2017a … verzeichnet?" — 40 s, the Klagsrückziehung
   form itself was not retrieved (F5); the answer took the Streitwert
   (EUR 9.340,14 / 11.340,14) and costs (EUR 2.509,10) from the Wiederaufnahme
   judgments of the same case and labelled them as such, so it is transparent
   but does not answer the question (form says EUR 13.340,14, costs ≈ EUR 81).
4. Ledger question in **fast** mode — 6 s, answered 383,99 from a different
   unit's ledger than the one sampled; both exist, the question did not name
   the unit and the agent did not mention that 49 such ledgers exist (F15).

New finding F23; recurring F2, F3, F4, F5, F7, F8, F9, F12, F13, F15, F16,
F18.

### Tick 6 — 2026-10-25 02:50 UTC (window 22:53 → 02:50, 3.95 h)

First tick after the redeploy and resume. Job: 3,960 of 338,114 files
(1.17 %; the total shrank by the 63 k documents the incremental filter now
skips), 18 files/min, ETA 13.7 days. Database: +2,894 documents (733 per
hour, half of the pre-restart pace), +38,132 chunks, totals 65,860 / 499,335;
47 documents under 200 chars; duplicate content 434 groups / 625 redundant
(22 %). Containers: backend 687 % CPU and 35.3 GB RSS, embedder 100 % busy,
MongoDB 9.4 GB.

Logs (backend, 133,175 lines): **553 timeouts on 451 distinct files**, 697
no-chunk files (163 of them `ATT0000x` placeholders seen before), 6,233 OSD
failures, 256 table-cell warnings, 4 invalid PDFs through the fallback (F21),
2 `reading_order_rb … something went wrong`, 2 "no active spec" warnings
(F24), 5 search 504s. The resumed job re-queued every file that had failed
before the restart (F23).

Retrieval probes (12): hybrid 3 / 6 / 7 at ranks 1 / 5 / 10 of 9 answered
(median 16.3 s), semantic 4 / 6 / 6 of 10 (median 15.1 s); 10 boilerplate
candidates skipped.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 47 s, **wrong for the first time**: "Geschäftszweig
   wird nicht explizit definiert"; the Firmenbuchabfrage was not among the
   sources, which were Finanzamt letters of the same client (F18 side effect
   or ranking drift, to watch).
2. Sentence lookup (`HubeTh_FradEd/DZ,-4.pdf`, a dental practice letterhead)
   — 60 s, correct.
3. Title summary "SonstigeErledigung_24668_122764_2026_05_13" — 88 s, not
   found, honest (F5).

Chat, hand-crafted:

4. "Welchen Auftrag hat das Landesgericht Krems an der Donau im Verfahren
   33 Cg 31/26f erteilt, wie hoch ist der Streitwert und innerhalb welcher
   Frist ist zu antworten?" — 88 s, order and four-week deadline correct, all
   sources from the right case folder, but "kein Streitwert genannt" although
   the order says EUR 87.000,00 (F22, F16).
5. "Welcher Betrag wurde im Verfahren 72 Cg 40/24t an den Sachverständigen
   angewiesen, aus welchen Kostenvorschüssen setzt er sich zusammen, und
   welcher Betrag geht an die Klagsvertreterin zurück?" — 78 s, **fully
   correct** (EUR 4.064,90 = 1.052 + 152 + 2.860,90; EUR 639,10 back), with
   the earlier EUR 1.000 Kostenvorschuss order as context.
6. Question 5 in **fast** mode — 8 s, **wrong**: reports the case's Streitwert
   (EUR 34.000,00) as the expert's fee and does not find the refund (F20, F11).

New finding F24; F23 raised to high; recurring F1, F2, F4, F5, F7, F8, F9,
F11, F12, F13, F16, F18, F20, F21, F22.

### Tick 7 — 2026-10-25 05:50 UTC (window 02:50 → 05:50, 3.0 h)

Job: 7,321 of 338,114 files (2.17 %), 18 files/min, ETA 13.0 days; the
retry tail has drained (5 timeouts in 3 h). Database: +3,291 documents
(1,097 per hour), +44,734 chunks, totals 69,151 / 544,069; 59 documents
under 200 chars; duplicate content 502 groups / 796 redundant (24 %).
Containers: backend 858 % CPU and **39.0 GB RSS**, embedder 100 % busy,
MongoDB 9.8 GB.

Logs (backend, 127,834 lines): 5 timeouts, 58 no-chunk files, 7,120 OSD
failures, 227 table-cell warnings, 1 PDF of unknown format through the
fallback (F21), 2 search 504s.

Retrieval probes (12): hybrid 6 / 7 / 9 at ranks 1 / 5 / 10 of 11 answered
(median 11.5 s), semantic 4 / 6 / 6 of 11 (median 7.4 s); 16 boilerplate
candidates skipped. Misses were a fee-note total line, a utility invoice's
SEPA footer and a letter opening, all near-boilerplate.

Chat, scripted (agent mode auto, 110–135 s each):

1. Control ALPO CONSULT — 118 s, **wrong again** (second tick in a row):
   the Firmenbuchabfrage is unchanged in the database but no longer in the
   top 10 of hybrid or text search for the question; copies of Finanzamt
   letters and a Ratenansuchen carrying the company name in their address
   block fill the list (F25, F4).
2. Sentence lookup (`FrisNa_JiscHe/Beilagen BP/DZ, Beilage, Arztbrief des
   Gelenkzentrums 11 …`) — 135 s, correct.
3. Title summary "Vorbereitende Schriftsatz der beklagten Partei_55500" —
   110 s, not found; the agent summarised "similar procedure types" instead
   (F5).

Chat, hand-crafted:

4. "Wie hoch war laut der E-Mail von Arealis vom 6. März 2020 das
   unverbindliche Guthaben für die Wohnung Svetelskystraße 16/11/8 per
   16.02.2019, und welche gesetzliche Auszahlungsfrist wird genannt?" — 54 s,
   correct (8.509,68 Euro, acht Wochen), with the e-mail's caveats.
5. "Wie hoch ist die Honorarnote Nr. 20/16 … insgesamt, wie viele Stunden
   wurden verrechnet und wie hoch ist die Zwischensumme vor Umsatzsteuer?" —
   106 s, correct (3.605,04; 20:00 h; 3.004,20; 20 % USt 600,84); the
   breakdown misnames the EUR 3.000 discount (F20, minor).
6. Question 4 in **fast** mode — 42 s, correct and concise.

New finding F25; recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18,
F20, F21.

### Tick 8 — 2026-10-25 08:50 UTC (window 05:50 → 08:50, 3.0 h)

Job: 11,008 of 338,114 files (3.26 %), 19 files/min, ETA 12.2 days, no
timeouts. Database: +3,606 documents (1,200 per hour), +45,953 chunks,
totals 72,757 / 590,022; 63 documents under 200 chars (mostly photo
collections); duplicate content 458 groups / 673 redundant (19 %).
Containers: backend 299 % CPU and 38.0 GB RSS, embedder 100 % busy,
MongoDB 10.4 GB.

Logs (backend, 126,228 lines): 0 timeouts, 72 no-chunk files, 7,125 OSD
failures, 200 table-cell warnings, 7 Docling `reading_order_rb … something
went wrong` errors on scanned pages (the pages still convert), 1 invalid PDF
(F21), 1 provider-test 401 from the System page (F18 note).

Retrieval probes (12): **14 of 24 searches hit the 30 s request timeout**;
of the answered ones hybrid 2 / 3 / 3 at ranks 1 / 5 / 10 of 4 (median
30.0 s), semantic 2 / 3 / 4 of 6 (median 27.9 s). The worst search
availability so far (F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 48 s, partially right: "Unternehmensberatung"
   only (the extract says Unternehmensberatung, Buchhaltung und Handel); the
   Firmenbuchabfrage was back among the 10 sources but below rank 5 (F25).
2. Sentence lookup (`Clinic_SkodaOctavia/Beilagen/I Korrespondenz
   Wiederbeschaffungswert.pdf`) — 111 s, correct.
3. Title summary "Ladung_21921_106616_2021_01_12" — 89 s, not found, honest
   (F5).

Chat, hand-crafted (legal literature this time):

4. "Welches Mindeststammkapital nennt der Leitfaden 'Die österreichische
   GmbH – Wesentliche Merkmale und Gründung' (Greiter/Pegger/Kofler 2009),
   und welche Funktion hat das Stammkapital laut dem Beitrag?" — 71 s,
   EUR 35.000 correct with the half-payment rule, but the function
   ("Haftungsfonds für die Gläubiger", stated in the same paragraph) was
   declared absent and replaced by derived functions from other sources
   (F22). Four copies of two GmbH articles took all 10 source slots (F4).
5. "Seit wann besteht die monatliche Beitragsgrundlagenmeldung (mBGM) laut
   dem WEKA-Fachbeitrag von Futterknecht/Doganova, und welche Funktion
   erfüllt die erste mBGM nach der Anmeldung?" — 123 s, **fully correct**
   (01.01.2019; fulfils the registration duty), with sanctions and special
   cases from the sibling article, labelled as such.
6. Question 4 in **fast** mode — 55 s, **wrong**: "nennt kein spezifisches
   Mindeststammkapital", because fast mode only received the guide's
   table-of-contents chunk; it then quoted EUR 35.000 from a § 9a GmbHG
   commentary with a misleading framing (F11, F22).

Recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18, F21, F22, F25; no
new finding.

### Tick 9 — 2026-10-25 11:50 UTC (window 08:50 → 11:50, 3.0 h)

Job: 14,033 of 338,114 files (4.15 %), 18 files/min, ETA 12.4 days, 10
timeouts. Database: +2,923 documents (974 per hour), +39,264 chunks, totals
75,680 / 629,286; 47 documents under 200 chars; duplicate content 434 groups
/ 658 redundant (23 %). Containers: backend 199 % CPU and **42.0 GB RSS**
(new high), embedder 100 % busy, MongoDB 11.3 GB.

Logs (backend, 105,867 lines): 10 timeouts, 79 no-chunk files, 6,757 OSD
failures, 246 table-cell warnings, and **4 request timeouts on `GET
/health`** (one logged as HTTP 504 to the Docker health check; the container
stayed "healthy" because the failures were not consecutive).

Retrieval probes (12): 18 of 24 searches hit the 30 s limit; the 3 answered
per index were all rank 1.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 88 s, **wrong** (third failure in four ticks): the
   Firmenbuchabfrage was not among the sources (F25).
2. Sentence lookup (`CZ-1849/webErv/Scheidungsvergleich.pdf`) — 62 s,
   correct.
3. Title summary "DZ, Beilage, 28Cg 34_14x - Replik Blg_2" — 129 s, 2
   iterations; the document (a near-empty cover sheet) was not found and the
   agent summarised the sibling "DZ, Sonstiges, Schriftsatz-4" as if it were
   the requested one (F5, F20).

Chat, hand-crafted:

4. "Welche Neuerungen bringt die WEG-Novelle 2022 beim Änderungsrecht des
   Wohnungseigentümers nach § 16 WEG laut dem immolex-Beitrag von Daniel
   Richter?" — auto 205 s (2 iterations, 29 k tokens), correct and thorough
   (Ladestationen, Barrierefreiheit, Zustimmungsfiktion § 16 Abs 5 WEG).
5. "Welchen Betrag macht das Evangelische Krankenhaus Wien im Zahlungsbefehl
   im Akt EKH/SimaHe gegen die beklagte Partei geltend, und wofür?" — 163 s,
   correct (EUR 7.563,40, Werklohn/Honorar, parties, Nebenintervenient), the
   case folder's own documents cited.
6. Question 4 in **fast** mode — 42 s, correct, with a labelled addition
   from a handbook chapter on cost allocation.

Recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18, F20, F25; no new
finding. The latency trend is the story of this tick: auto-mode answers now
take 2.7–3.4 minutes while the embedder is saturated.

### Tick 10 — 2026-10-25 14:50 UTC (window 11:50 → 14:50, 3.0 h)

Job: 16,748 of 338,114 files (4.95 %), 17.8 files/min, ETA 12.7 days, 1
timeout. Database: +2,623 documents (874 per hour), +40,736 chunks, totals
78,303 / 670,022; 46 documents under 200 chars; duplicate content 404 groups
/ 573 redundant (22 %). Containers: backend 339 % CPU and 37.0 GB RSS (down
from 42), embedder 100 % busy, MongoDB 11.4 GB.

Logs (backend, 116,394 lines): 1 timeout, 92 no-chunk files, 7,844 OSD
failures, 226 table-cell warnings, 18 HTTP 504 on search, 5 "no active spec"
warnings (F24). Two copies of a Schriftsatz with doubled letters (F19) and
an EU treaty PDF with mis-mapped umlauts (F6 variant).

Retrieval probes (12): 20 of 24 searches hit the 30 s limit; 2 answered per
index, 1 of 2 in the top 5.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 65 s, **wrong** (F25, 4 of the last 5 ticks).
2. Sentence lookup (an invoice courtesy line shared by several invoices) —
   35 s, correct: it listed the three invoices that carry the sentence.
3. Title summary "Schummer_Kriwanek,_Das_neue_Unternehmensgese…" — 105 s,
   not found, honest (F5).

Chat, hand-crafted:

4. "Um welchen Streitwert geht es im Verfahren 4 Cg 65/21b vor dem
   Landesgericht Linz, und was hat die klagende Partei mit dem vorbereitenden
   Schriftsatz vom 09.11.2021 vorgelegt?" — 83 s, the Schriftsatz
   (EUR 28.710,00 s.A., Urkundenvorlage) was not retrieved; the agent cited
   the case's other filings and said the Streitwert is not in them. Honest
   miss (F17, F16).
5. "Welche Eckdaten enthält das verbindliche Mietanbot für die Praxis 1080
   Wien, Alser Straße 39/17: Mietbeginn, Bindungsfrist, Hauptmietzins,
   Betriebskosten, mietfreie Zeit und Kaution?" — 138 s, all figures correct
   (01.03.2026; 31.03.2026; EUR 2.065; EUR 593; three rent-free months;
   EUR 9.000) and it flagged that the contract summary names 01.04.2026 as
   the start; one error: it says the landlord is bound by the offer, the
   tenant is (F20).
6. Question 5 in **fast** mode — 48 s, five figures right, "mietfreie Zeit
   nicht enthalten" wrong (F22), same role swap (F20).

Recurring F1, F4, F5, F6, F7, F8, F9, F11, F12, F13, F16, F17, F18, F19,
F20, F22, F24, F25; no new finding.

### Tick 11 — 2026-10-25 17:50 UTC (window 14:50 → 17:50, 3.0 h)

Job: 19,630 of 338,114 files (5.81 %), 17.5 files/min, ETA 12.8 days, 1
timeout. Database: +2,783 documents (928 per hour), +39,144 chunks, totals
81,087 / 709,172; 58 documents under 200 chars; duplicate content 380 groups
/ 554 redundant (20 %). Containers: backend 1,435 % CPU and **44.4 GB RSS**
(highest yet), embedder 41 % CPU, MongoDB 131 % CPU in the snapshot (no
long-running operation visible in `currentOp`; the db-stats sampler takes
200–300 ms every 30 min).

Logs (backend, 120,349 lines): 1 timeout, 88 no-chunk files, 7,708 OSD
failures, 223 table-cell warnings, 6 "not valid" PDFs (six copies of one
file) through the fallback (F21, F26), 4 search 504s.

Retrieval probes (12): 8 answered per index (4 lost to 504s), hybrid and
semantic both 6 / 7 / 7 at ranks 1 / 5 / 10, medians 2.7 s: the best
retrieval numbers since tick 2, with the embedder only 41 % busy on
text-heavy literature.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 81 s, **wrong** (F25, 5 of the last 6 ticks).
2. Sentence lookup (`FP-12-20/Recherche/Vergleich/AußStrG, § 30 AußStrG
   Vergleich.pdf`) — 150 s, correct.
3. Title summary "ZVB 2020_27_ OGH 18.11.2019, 8 Ob 88_19b …" — 127 s,
   **found and correctly summarised** (the title's words recur in the body),
   the first title-summary success since tick 2.

Chat, hand-crafted:

4. "Worum geht es im Verfahren 14 C 995/23z vor dem Bezirksgericht Wiener
   Neustadt (Schrack Technik GmbH gegen Elektrotec GmbH): Streitwert,
   Parteienvertreter und Datum des vorbereitenden Schriftsatzes?" — 137 s,
   correct (EUR 32.327,66; BLS Rechtsanwälte GmbH / Dr. Fischerlehner;
   25.11.2025), all 10 sources from the case folder, and it flagged a
   different figure (EUR 33.773,69) in the Einspruch.
5. "Welche Klage wurde am 10.05.2022 für die Eigentümer der Liegenschaft KG
   01660 EZ 858 beim Bezirksgericht Donaustadt eingebracht, welcher
   Mietzinsrückstand wird eingeklagt und wie hoch ist der monatliche
   Mietzins?" — 67 s, correct and complete (Mietzins- und Räumungsklage with
   pfandweise Beschreibung; EUR 1.790,24 = 1.695,36 + 32,14 + 62,74; rent
   EUR 423,84; object data; the later Räumungsexekution).
6. "Was sagt die Stellungnahme von Aquatherm vom 10.03.2014 zu den
   Wohnungsstationen der Firma Thermograf …?" in **fast** mode — 37 s,
   correct (since 1985; above the WC cistern when there is no technical
   room).

New finding F26 (error placeholders stored as documents); recurring F1, F4,
F5, F7, F8, F9, F11, F12, F13, F16, F17, F18, F21, F25. The three
hand-crafted answers were the best of the whole run.

### Tick 12 — 2026-10-25 20:50 UTC (window 17:50 → 20:50, 3.0 h)

Job: 22,069 of 338,114 files (6.53 %), 17.0 files/min, ETA 13.1 days, 5
timeouts. Database: +2,357 documents (786 per hour), +35,056 chunks, totals
83,443 / 744,222; 45 documents under 200 chars; duplicate content 386 groups
/ 554 redundant (24 %). Containers: backend 536 % CPU and 42.1 GB RSS,
embedder 100 % busy, MongoDB 12.5 GB.

Logs (backend, 97,266 lines): 5 timeouts, 70 no-chunk files, 7,074 OSD
failures, 312 table-cell warnings, 3 more copies of the unreadable
`w_leuthner_…pdf` through the fallback (F21, F26). The search rate limiter
stalled the probes three times (F14).

Retrieval probes (12): hybrid 4 / 5 / 6 at ranks 1 / 5 / 10 of 9 answered
(median 4.9 s), semantic 3 / 5 / 6 of 10 (median 1.2 s).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 126 s, **wrong** (F25, 6 of the last 7 ticks).
2. Sentence lookup (`SperWo_Unter/sperlich-1.pdf`, a letter stored three
   times) — 85 s, correct, names all three copies.
3. Title summary "Bescheid über die Festsetzung von Aussetzungszinse" —
   51 s, found; the agent said several such Bescheide of different taxpayers
   exist and listed each with its own period and amount (F15, handled well).

Chat, hand-crafted:

4. "Welcher offene Gesamtbetrag wird im Aufforderungsschreiben vom 02.06.2021
   an die Vereinigung für Demokratie in Afrika genannt, welches
   Kautionsguthaben wurde gegengerechnet und welche Ratenzahlung wird
   angeboten?" — 67 s, **fully correct** (EUR 16.590,80; EUR 6.024,33;
   17 instalments of EUR 1.000 with a final EUR 976,71; Terminsverlust;
   10-day acceptance), and it noticed that two versions of the letter name
   different start months.
5. "Welche aktuellen Entscheidungen zur kurzfristigen Vermietung nennt der
   ZVB-Beitrag 2021/38 von Klaus Pfeiffer, und welche Rechtsgebiete behandelt
   der Beitrag?" — 103 s, correct and detailed (the 30-day definition, the
   LVwG Tirol and VwGH decisions, footnoted case law).
6. Question 4 in **fast** mode — 58 s, total and instalments right, the
   Kautionsguthaben declared absent (F22, F11).

Recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F14, F15, F16, F18, F21,
F22, F25, F26; no new finding.

### Tick 13 — 2026-10-25 23:50 UTC (window 20:50 → 23:50, 3.0 h)

Job: 24,237 of 338,114 files (7.17 %), 16.4 files/min, ETA 13.4 days, 6
timeouts. Database: +2,073 documents (691 per hour, the slowest window since
the restart), +34,440 chunks, totals 85,516 / 778,662; 33 documents under
200 chars; duplicate content 291 groups / 436 redundant (21 %). Containers:
backend 300 % CPU and 40.9 GB RSS, embedder 100 % busy, MongoDB 13.0 GB.

Logs (backend, 98,397 lines): 6 timeouts, 91 no-chunk files, 7,075 OSD
failures, 115 table-cell warnings, **9 embedder HTTP 500s** between 21:57
and 22:05 (F27, retried without loss), 3 rate-limit stalls (F14), 5 "no
active spec" warnings (F24).

Retrieval probes (12): 18 of 24 searches hit the 30 s limit; of the answered
ones, 1 of 2 hybrid and 1 of 4 semantic in the top 5.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 172 s, **correct** (2 passes in the last 8 ticks;
   the extract was among the sources below rank 5, F25).
2. Sentence lookup (`SektGE_AtelierGE/DZ, vorbereitender Schriftsatz,
   TM-22_13, Konvolut.pdf`) — 80 s, 2 iterations, correct.
3. Title summary "Ärztliches Zeugnis" — 79 s, the document was not found and
   the agent described the document type from Vorsorgevollmacht templates
   without saying so (F5, F20).

Chat, hand-crafted:

4. "Welche Leistungsgruppen und Teilsummen enthält das Angebot Nr.
   609633-106034 der Soluto GmbH vom 19.04.2021 …, wer ist der Bearbeiter
   und welche Versicherung ist genannt?" — **274 s** (F18), **fully
   correct**: all seven section sums (they add up to the stated net
   EUR 5.338,26), VAT, handler, insurer.
5. "Wie hoch ist die Honorarnote Nr. 16/2017 von Dr. Trautmann an die Grazer
   Wechselseitige Versicherung AG insgesamt, und wie setzt sie sich
   zusammen?" — 138 s, header data correct (client, Gegner, Causa,
   Bemessungsgrundlage), but "Gesamtsumme nicht explizit ausgewiesen": the
   note's amounts table (EUR 220,30 total) is a separate small chunk that
   was not retrieved (F22, F16); the answer then padded with the "übliche
   Kostenstruktur" of other notes (F20).
6. Question 5 in **fast** mode — 83 s, same absence claim.

New finding F27; recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F14, F16,
F18, F20, F22, F24, F25.

### Tick 14 — 2026-10-26 02:50 UTC (window 23:50 → 02:50, 3.0 h)

Job: 26,279 of 338,114 files (7.77 %), 15.8 files/min, ETA 13.8 days, 2
timeouts. Database: +1,946 documents (648 per hour, the slowest window of
the run), +33,824 chunks, totals 87,463 / 812,492; 54 documents under 200
chars; duplicate content 302 groups / 427 redundant (22 %). Containers:
backend 551 % CPU and 37.9 GB RSS, embedder 96 % busy, MongoDB 13.5 GB.

Logs (backend, 108,392 lines): 2 timeouts, 92 no-chunk files, **9,018 OSD
failures** (the most in a window), 133 table-cell warnings, 4 more copies of
the unreadable `w_leuthner_…pdf` (F21, F26), 18 search 504s, 5 "no active
spec" warnings (F24).

Retrieval probes (12): **21 of 24 searches hit the 30 s limit**, the three
answered probes ranked 1, 1 and 2.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 214 s, **correct** (second pass in a row, 3 of the
   last 9).
2. Sentence lookup (`KucePa_MichWo/Kucera Praxisinfo.pdf`) — 264 s, 2
   iterations, correct.
3. Title summary "Direktzustellung_23198_114708_2023_07_11" — 45 s, it
   summarised the ERV sibling "…114707" as if it were the requested file
   (F5, F20).

Chat, hand-crafted (legal literature):

4. "Welche Strafdrohung sieht § 146 StGB laut dem Normtext im Wiener
   Kommentar Höpfel/Ratz vor, wer ist der Autor der Kommentierung und
   welchen Stand hat sie?" — 112 s, author and both editions' dates correct
   (Kirchbacher, Stand 1.9.2011; Kirchbacher/Sadoghi, Stand 1.3.2019), but
   "keine explizite Angabe zur Strafdrohung": the Normtext chunk right after
   the retrieved header was not fetched (F22).
5. "Welche Frage behandelt der jusIT-Beitrag 2020/71 von Thomic und Zopf,
   auf welche OGH-Entscheidung bezieht er sich und welche Deskriptoren nennt
   er?" — 48 s, **fully correct**, all eight descriptors.
6. Question 4 in **fast** mode — 98 s, same answer and the same absence
   claim.

Recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18, F20, F21, F22,
F24, F25; no new finding.

### Tick 15 — 2026-10-26 05:50 UTC (window 02:50 → 05:50, 3.0 h)

Job: 28,114 of 338,114 files (8.31 %), 15.3 files/min, ETA 14.2 days, 9
timeouts. Database: +1,761 documents (587 per hour, the slowest window yet),
+28,566 chunks, totals 89,223 / 841,052; 45 documents under 200 chars;
duplicate content 219 groups / 311 redundant (18 %). Containers: backend
780 % CPU and 36.2 GB RSS, embedder 100 % busy, MongoDB 13.8 GB at 47 % CPU.

Logs (backend, 88,115 lines): 9 timeouts, 67 no-chunk files, 7,509 OSD
failures, 127 table-cell warnings, 4 "no active spec" warnings (F24), one
more error-placeholder document (F26).

Retrieval probes (12): **0 of 24 searches answered**, all hit the 30 s
limit; the first complete blackout of the HTTP search path. Because no probe
hit, the scripted sentence-lookup prompt was skipped.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 156 s, half right ("Unternehmensberatung" from
   Honorarnoten, the extract not among the top sources, F25).
2. Title summary "Unfallbericht" — 154 s, 2 iterations; a survey of several
   accident reports without naming the cases (F15).

Chat, hand-crafted:

3. "Welche Positionen und Summen enthält das Auftragsschreiben Nr. 009/2010
   … wie hoch ist die Gesamtsumme inklusive Mehrwertsteuer …?" — **HTTP 504
   after 300 s**, the sessions endpoint's own request limit (F18). No
   answer.
4. "Was bestimmt § 23 IO laut dem Normtext im Kommentar GeKo Wohnrecht …,
   wer ist der Autor der Kommentierung und welchen Stand hat sie?" — 229 s,
   2 iterations; author (Mohr) and Stand (1.10.2017) correct, the Normtext
   itself not given (F22).
5. Question 4 in **fast** mode — 76 s, same facts, and it said explicitly
   that the Normtext was not in the delivered excerpt (F22, well phrased).

Recurring F1, F4, F7, F8, F9, F11, F12, F13, F15, F16, F18, F22, F24, F25,
F26; no new finding. This window is the low point of the run for search
availability and chat latency.

### Tick 16 — 2026-10-26 08:50 UTC (window 05:50 → 08:50, 3.0 h)

Job: 29,770 of 338,114 files (8.80 %), 14.7 files/min, ETA 14.6 days, 8
timeouts; the rate has fallen every tick since tick 8. Database: +1,585
documents (530 per hour), +28,395 chunks, totals 90,808 / 869,447; 35
documents under 200 chars; duplicate content 216 groups / 307 redundant
(19 %). Containers: backend 394 % CPU and 39.6 GB RSS, embedder 100 % busy,
MongoDB 14.3 GB.

Logs (backend, 92,898 lines): 8 timeouts, 69 no-chunk files, 7,827 OSD
failures, 110 table-cell warnings, 25 HTTP 504 on search, 4 "no active spec"
warnings (F24).

Retrieval probes (12): 0 of 24 answered, the second consecutive blackout.
Because no probe hit, the sentence-lookup prompt was skipped again.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 139 s, **wrong** (F25): "Geschäftszweig nicht
   genannt", and a letter's addressee presented as the company's manager.
2. Title summary "2020-07-02 Gutachten _6 WM026-20" — 127 s, summarised from
   the surveyor's cover letters and a later contract of the same matter, not
   from the Gutachten (F5).

Chat, hand-crafted:

3. "Welche Ansprüche macht die Klägerin in der Klage gegen die ATV Privat TV
   GmbH & Co KG (GZ 18 Cg 65/16x) … geltend, wie werden sie bewertet und wie
   hoch ist der Gesamtstreitwert?" — 73 s, **fully correct** (Unterlassung
   EUR 31.000, Urteilsveröffentlichung EUR 1.000, Entgelt EUR 600, total
   EUR 32.600, §§ 3 and 73f UrhG); 9 of the 10 sources were the three copies
   of the template (F4).
4. "Über welchen Darlehensbetrag bestätigt die Pfandurkunde der … Wien-Süd …,
   wie hoch sind Verzinsung und Verzugszinsen, und welche Liegenschaft wird
   verpfändet?" — **HTTP 504 after 300 s** (F18), the second chat timeout of
   the run.
5. Question 3 in **fast** mode — 131 s, correct.

Recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18, F24, F25; no new
finding.

### Tick 17 — 2026-10-26 11:50 UTC (window 08:50 → 11:50, 3.0 h)

Job: 31,135 of 338,114 files (9.21 %), 14.1 files/min, ETA 15.2 days, 11
timeouts. Database: +1,307 documents (436 per hour, the slowest window of
the run), +25,481 chunks, totals 92,115 / 894,928; 24 documents under 200
chars; duplicate content 191 groups / 309 redundant (24 %). Containers:
backend 304 % CPU and **45.5 GB RSS** (new high), embedder 100 % busy,
MongoDB 14.5 GB.

Logs (backend, 85,653 lines): 11 timeouts, 59 no-chunk files, 8,020 OSD
failures, 138 table-cell warnings, 25 HTTP 504 on search, **2 Lucene
clause-limit failures of the text stage** (F28, triggered by the loop's own
long questions at 09:07 and 09:15 UTC), 4 "no active spec" warnings (F24).

Retrieval probes (12): 1 of 24 answered (semantic, rank 1); the rest timed
out.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 223 s, **wrong** (F25).
2. Title summary "Klient Einheitssatz, Abrechnung bis 12_03_2019" — 169 s,
   not found; the agent said so and listed similar Einheitssatz Abrechnungen
   of other clients with a disclaimer (F5, handled honestly).

Chat, hand-crafted:

3. "Was regelt § 157 UGB laut dem Normtext im Großkommentar Zib/Dellinger …
   ?" — 195 s, **fully correct** (Anmeldung des Erlöschens zum Firmenbuch,
   Verwahrung der Bücher bei einem Gesellschafter oder Dritten, gerichtliche
   Bestimmung, Einsichts- und Benutzungsrecht der Gesellschafter und Erben),
   with labelled GmbH-law context.
4. "Welche anweisbare Summe weist die Schlussrechnung Nr. 041114 der ARE …
   aus …?" — 268 s, 2 iterations; the figures come from the client's
   Rechnungsprüfbericht (EUR 29.193,34 after a 3 % Haftrücklass, advances
   EUR 103.623,06, Zwischensumme and VAT right) but are attributed to the
   Schlussrechnung, whose own payable line says EUR 33.301,06; the
   discrepancy was not mentioned (F20).
5. Question 3 in **fast** mode — 78 s, fully correct.

New finding F28; recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18,
F20, F24, F25.

### Tick 18 — 2026-10-26 14:50 UTC (window 11:50 → 14:50, 3.0 h)

Job: 32,553 of 338,114 files (9.63 %), 13.7 files/min, ETA 15.6 days, 19
timeouts on 16 files. Database: +1,300 documents (433 per hour), +26,451
chunks, totals 93,415 / 921,379; 51 documents under 200 chars; duplicate
content 197 groups / 300 redundant (23 %). Containers: backend 200 % CPU and
44.2 GB RSS, embedder 100 % busy, MongoDB 14.7 GB.

Logs (backend, 86,438 lines): 19 timeouts, **118 no-chunk files** (scanned
court exhibits that OCR to nothing), 7,740 OSD failures, 96 table-cell
warnings, 23 HTTP 504 on search, 4 "no active spec" warnings (F24).

Retrieval probes (12): 0 of 24 answered, the third blackout.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 104 s, half right ("Beratung", inferred from a
   letter's address line; F25).
2. Title summary "DZ, Beilage, 2" — **HTTP 504 after 300 s**, the first
   scripted chat lost to the limit (F18).

Chat, hand-crafted:

3. "Unter welchen Voraussetzungen darf laut der Kanzlei-Stellungnahme vom
   29.02.2024 ein Wohnungseigentümer eine Klimaanlage am Dach installieren
   …?" — auto mode **HTTP 504 after 300 s**.
4. "Was bestimmt § 3 MaklerG laut dem Normtext im Kommentar GeKo Wohnrecht
   II, wer kommentiert die Bestimmung und welchen Stand hat sie?" — auto
   mode **HTTP 504 after 300 s**.
5. Question 3 in **fast** mode — 146 s, the core points right (OGH
   5 Ob 24/08b, "fast notwendig" standard, shading before cooling, mildest
   location), the § 16 WEG criteria and the second decision (5 Ob 160/14m)
   omitted.

Recurring F1, F4, F7, F8, F9, F11, F12, F13, F16, F18, F24, F25; no new
finding. Three of four auto-mode chats timed out: under the current embedder
load the default agent mode no longer answers within the API's limit.

### Tick 19 — 2026-10-26 17:50 UTC (window 14:50 → 17:50, 3.0 h)

Job: 33,726 of 338,114 files (9.97 %), 13.2 files/min, ETA 16.1 days, 23
timeouts on 19 files. Database: +1,121 documents (374 per hour, the slowest
window of the run), +22,594 chunks, totals 94,536 / 943,973; 23 documents
under 200 chars; duplicate content 165 groups / 236 redundant (21 %).
Containers: **backend 0.5 % CPU** and 40.3 GB RSS, embedder 100 % busy,
MongoDB 15.3 GB: the job is now bound by the embedder (F29).

Logs (backend, 85,452 lines): 23 timeouts, 52 no-chunk files, 8,781 OSD
failures, 170 table-cell warnings, 27 HTTP 504 on search, 4 "no active spec"
warnings (F24).

Retrieval probes (12): 0 of 24 answered, the fourth blackout.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 297 s, half right ("Unternehmensberatung" from a
   Honorarnote; F25).
2. Title summary "IMG_20210920_0005" — 298 s, not found, honest refusal
   (F5).

Chat, hand-crafted:

3. "Welche Eckdaten enthält der Erstentwurf des Mietvertrages zwischen der
   MICRO Beteiligungs GmbH und der die Berater Unternehmensberatungs GmbH
   …?" — auto mode **HTTP 504 after 300 s**.
4. "Was beantragt die Eigentümergemeinschaft EZ 1474 KG 01209 in ihrer
   Rekursbeantwortung vom 15.10.2018 im Verfahren 5 MSch 13/15h, worum geht
   es, und auf welche Entscheidungen des LG für ZRS Wien stützt sie sich?" —
   273 s; motion and subject (Rücklagenüberschuss, Streitwert EUR 21.748)
   right, but the arguments came from a second Rekursbeantwortung in the
   same folder (radiator maintenance) and the two LG decisions were not
   named (F20).
5. Question 3 in **fast** mode — 95 s; object (494 m²) and deposits right,
   start, duration and rent declared absent although the Erstentwurf states
   them (F22), segments taken from a later version (F20).

New finding F29; recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F18,
F20, F22, F24, F25.

### Tick 20 — 2026-10-26 20:50 UTC (window 17:50 → 20:50, 3.0 h)

Job: 34,590 of 338,114 files (10.23 %), 12.6 files/min, ETA 16.8 days, 25
timeouts on 21 files. Database: +832 documents (277 per hour, the slowest
window of the run), +19,072 chunks, totals 95,368 / 963,045; 17 documents
under 200 chars; duplicate content 128 groups / 195 redundant (23 %).
Containers: backend 785 % CPU (an OCR-heavy batch again) and 37.5 GB RSS,
embedder 100 % busy, MongoDB 15.6 GB.

Logs (backend, 71,509 lines): 25 timeouts, 29 no-chunk files, 7,543 OSD
failures, 78 table-cell warnings, 25 HTTP 504 on search, 4 `GET /health`
timeouts (F7), 4 "no active spec" warnings (F24).

Retrieval probes (12): 0 of 24 answered, the fifth blackout.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — **HTTP 504 after 300 s**, the first time the
   control itself timed out (F18).
2. Title summary "Fotomontage samt E-Mail" — 216 s, described from the
   Klagebeantwortung drafts that cite it as evidence, not from the document
   (F5, F20).

Chat, hand-crafted:

3. "Zu welcher Schlussfolgerung kommt das Gutachten Nr. GV_1/20 vom
   20.05.2021 zur Haarentfernung mittels IPL- und Lasergeräten durch
   Kosmetikerinnen (Ärztevorbehalt), und auf welche Rechtsgrundlagen stützt
   es sich?" — auto 152 s, **wrong in substance**: only the opinion's cover
   page was retrieved; the answer then presented the firm's own 2022 letter
   to the WKO, which argues the opposite (Ärztevorbehalt), as the opinion's
   "decisive classification". The Gutachten concludes that photoepilation
   is not reserved to physicians (F20, F22).
4. "Was wurde im Notariatsakt über die Änderungen des Gesellschaftsvertrages
   der Gesellschaft FN 319646v beschlossen …?" — 164 s; the option and
   voting clauses came from the right deed, but the capital increase to
   EUR 40.000 and the new shareholder were missed and replaced by clauses of
   two other companies' deeds (F17, F20).
5. Question 3 in **fast** mode — 192 s, honest: "lediglich die Überschrift
   … Schlussfolgerungen fehlen" (F22).

Recurring F1, F4, F5, F7, F8, F9, F11, F12, F13, F16, F17, F18, F20, F22,
F24, F25; no new finding. The expert-opinion answer is the most damaging
one of the run: fluent, cited, and the reverse of the source.

### Tick 21 — 2026-10-26 23:50 UTC (window 20:50 → 23:50, 3.0 h)

**PR #29 went live in this window** (2026-10-26 23:18 UTC, images
`:findings-fix` = main `c475781`, rollback tags `rollback-20261027`; the loop
files are committed as `c475781`). The backend restart marked job b98d150e
interrupted and the API resumed it: discovery from scratch (11 min), then a
queue of 304,723 files = the 338,114 minus the 35,666 already ingested (F23
now skips them and the recorded failures; the counters restart from 0).

Job (new image, 0.5 h in): 942 of 304,723 files, 45.8 files/min (inflated by
425 skipped duplicates), per-extension ETA 4.7 d (`.pdf` 25/min from its own
window, the rest on the overall pace until they get one). Database: +1,235
documents, +31,830 chunks (totals 96,603 / 994,875); chunk stats unchanged
(avg 304 tokens, max 514): the resumed job keeps the `max_tokens: 512` of its
stored config, the 1,024 budget applies to jobs started from now on.
Containers: backend 1,575 % CPU / 29 GB (16 conversion processes), embedder
0.2 % CPU / 6.3 GB after its restart, MongoDB 15.7 GB.

New-image checks: 0 conversion-worker kills, 0 timeouts, 0 OSD "Too few
characters" errors (7,543 last tick), backend log 12,926 lines (71,509 last
tick); `ingestion_stats` and `failed_documents` are written during the run
(27 flushes; 183 `image_only_pdf` + 136 `no_chunks` visible in
`/failed-documents/summary`); 43 pure scans OCRed successfully; no new
`[Error:` placeholder documents (the 42 existing ones all predate the deploy;
`quellexctl ingest purge-placeholders` not run yet, F26).

Retrieval probes (12): hybrid hit@1 6, hit@5 10, hit@10 11, median 535 ms;
semantic hit@5 6, median 160 ms — the first full probe set since tick 15
(five blackouts in between). The tick's own probes still hit the search rate
limit three times (F14).

Chat, scripted (agent mode auto, gemma4:12b):

1. Control ALPO CONSULT — **96 s, correct**, all five sources the FN 404945h
   extract (F25 fix live).
2. Sentence lookup (policy clause) — 125 s, correct document named, three of
   five sources unrelated insurance documents.
3. Title summary "Vollmacht Lemiere inkl Übersetzung" — 127 s, content
   correct (DECATHLON SE power of attorney), but the sources list leads with
   unrelated documents (F5).

Chat, hand-crafted:

4. "Wer hat gegen die APMEA Bauplanung und Baumanagement GmbH beim
   Handelsgericht Wien einen Zahlungsbefehl über EUR 40.000 erwirkt, und mit
   welchen Argumenten bestreitet die beklagte Partei die Forderung im
   Einspruch vom 01.07.2022?" — 78 s, **miss**: the Einspruch itself was not
   retrieved (its text is word-scrambled, F33); the answer says the documents
   give no direct hint, then correctly places the matter ("Mahnklage
   Gobergasse 17", 62 Cg 31/22d) from the Honorarnoten. Sources: four
   Honorarnoten and five copies of one unrelated bank statement (F4).
5. "Welche Beträge macht Mag. Michael Speldrich in der Mietzins- und
   Räumungsklage gegen Samah Alfageer-Shamiya vor dem Bezirksgericht Hernals
   vom 28.07.2026 geltend, und um welche Wohnung geht es?" — 59 s,
   **correct and careful**: EUR 11.766,18 (28.07. draft) vs 12.506,82 (10.08.
   filed version) vs 13.988,10 (Klagsausdehnung 22.10.), Räumung EUR 750,
   Neulerchenfelder Straße 17/2/43; every source from the right matter.
6. "Was regelt § 3 MRG laut der Zusammenfassung des Mietrechtsgesetzes idF
   WRN 2015, und welche Erhaltungsarbeiten fallen darunter?" — 59 s,
   **correct**, cites the summary and the firm's § 3 MRG research memo.

Latency 59–127 s per question (150–300 s and a 504 last tick), 17–21 k
prompt tokens (F11).

New: F30 (CCITT fax scans OCR empty), F31 (worker processes rewrite
profiles.yaml; profile_key "default"), F32 (invalid UTF-8 → embedder 400),
F33 (scrambled word order in a WebERV PDF). Recurring F4, F5, F7 (the resumed
job still runs in the API process), F11, F12, F13, F14, F16, F23, F26. Not
seen this tick: F1, F8, F9, F18, F20, F22, F24, F25.
#### Tick 21 addendum — 00:15–00:45 UTC, second pass on the same window

Job at 00:15 UTC: 1,421 of 304,723 files, 39.9 files/min (45.6 at 23:50),
535 image-only PDFs, 55 OCRed scans, 192 no-chunk files, 475 duplicate skips,
0 timeouts, per-extension ETA 5.2 d — only `.pdf` had a window pace yet
(28/min); the other 25 extensions still run on the overall pace, so the
figure is mostly guesswork until the queue reaches them. Backend 1,873 % CPU
and 37 GB RSS (16 conversion processes plus the API), embedder 0.2 %,
MongoDB 16 %. One scripted hybrid probe failed with HTTP 500
`maxClauseCount is set to 1024` (F28); chunks still top out at 514 tokens
(F10, see F36).

Chat, hand-crafted (continued):

7. "Was regelt § 1 TKG 2021 (Zweck und Ziele) laut dem Gesetzestext in der
   Fassung vom 18.08.2022?" (auto) — 84 s, **wrong framing** (F34): the
   statute (`FP-DIETR-MAGENTA/Recherche/TKG 2021, Fassung vom 18.08.2022.pdf`,
   ingested 22:47 UTC) is semantic #1 and #2 for the question but missing
   from the hybrid top 10 the agent used; the answer opens "Ja, die Regelung
   … ist im TKG 2021 enthalten" and presents goals from the Jahrbuch
   Datenschutzrecht 2021 and Zankl's Rechtshandbuch as § 1's content.
8. "Welche Rechtsfolge hat es laut Andrew Annerl, 'Kostenvorschuss und
   Präklusion des Sachverständigenbeweises', wenn der Kostenvorschuss nicht
   erlegt wird, und welche ZPO-Bestimmungen werden herangezogen?" (auto) —
   66 s, **correct**: Präklusion des Beweismittels, § 365 and § 332 ZPO,
   cited from the article itself and Fasching/Konecny; four of the ten
   sources are copies of the article in four folders (F4, F15).
9. Question 7 in **fast** mode — 9 s, honest: "Konkrete Inhalte oder Ziele
   aus dem spezifischen Paragraphen § 1 TKG 2021 sind in den Dokumenten
   nicht aufgeführt" — the fast path misses the statute as well but says so,
   while auto filled the gap with literature (the same contrast as at ticks
   19 and 20).

Scanned pages the new image records as `image_only_pdf` (F35): 484 of the
first 539 pure scans (90 %). Twelve random ones re-OCRed with plain
Tesseract on a pypdfium2 render: 4 readable (15–209 words), 8 empty. The
pipeline's own converter on `Aufklärungsbogen Aesthetic.Clinic vom
16.6.2017.pdf` returns one `PictureItem` carrying 1,568 characters of OCR
text as its children and exports `<!-- image -->` only; `Beilage I.PDF` the
same (736 characters); `Direktzustellung_2009_03_24_4.pdf` comes back with
no items at all while Tesseract reads 209 words. The readable share of the
image-only records is therefore roughly a third, on top of the CCITT class
of F30.

Chunk budget (F36): the resumed job runs with the `max_tokens: 512` of its
stored request; new-image chunks max 514 tokens, 918 of 1,900 at ≥ 500.
Counters (F37): `image_only_pdfs_ocr` is 0 in the status with 55 OCRed scans
in `ingestion_stats`; `failed_files` 192 leaves the 535 image-only files out.

New (addendum): F34, F35, F36, F37. Recurring in the addendum: F4, F10, F11,
F15, F28, and the auto-versus-fast contrast of F22.

### Tick 22 — 2026-10-27 02:50 UTC (window 23:50 → 02:50, 3.0 h)

Job (new image, 3.5 h in): 2,987 of 304,723 files (0.98 %), +2,043 files in
the window, 14.9 files/min; `.pdf` 10.4/min from its own window; ETA 14.1 d
(the queue is in its PDF stretch; 889 PDFs converted at 163 s and 82 chunks
each on average, max 1,785 s). Database: +697 documents, +71,670 chunks
(totals 97,301 / 1,066,559); chunks avg 331 tokens, max 514 (still the
512-token budget of the resumed job). Containers: backend 1,722 % CPU and
55 GB (F38), embedder 100 % CPU / 9.5 GB / GPU 28 % (2,000 tok/s aggregate in
10 min, 3.6–5.9 k tok/s on a direct 2,283-token request after a 3 s first
call), MongoDB 17 GB.

Failures recorded during the run (F2 live): `image_only_pdf` 1,271,
`no_chunks` 298, `error` 1 (a `[Error: Could not read file ...]` now recorded
instead of stored, F26), 538 duplicates skipped (F4), 449 pure scans OCRed
successfully. 18 conversions hit the 1,800 s budget and were killed
(F1 fix: `Killing conversion worker N: cancelled (...)`, 0 zombies; the
retry log says "timeout increased to 1354s" but retries with 1,800 s —
cosmetic). Backend log 64,937 lines, 1,242 "No chunks created", 420 embedder
500s (F40), 10 rate-limit hits (F14), 8 `GET /health` timeouts (F7), 8
"no active spec" (F24), 2 search 500s `maxClauseCount` from the tick's own
long sentence probes (F28).

**F35 (high, the addendum's finding, verified here): 43 % of the processed PDFs are recorded as `image_only_pdf`,
and most of them are readable scans.** Verified on `Erlagschein.pdf`: direct
Tesseract on the rendered page reads 622 chars; the pipeline runs OCR but the
layout model makes the page a picture, the OCR words become picture children
and the Markdown export keeps only `<!-- image -->`. The rollback image does
the same, so this predates PR #29 (Docling 2.142 since the dependency
refresh). The new F9 accounting is what made it visible.

Retrieval probes (12): hybrid hit@5 8 / hit@10 9 of 10 (2 errors = the
`maxClauseCount` 500s), **median 10.4 s**; semantic hit@5 8, median 13.2 s —
query latency is back to the F18 level while the embedder serves the big
PDF batches (535 ms / 160 ms at tick 21, when the embedder was idle).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 86 s, answer correct (Unternehmensberatung,
   Buchhaltung und Handel, cited to the Firmenbuchabfrage), but the sources
   list holds the Steuerkonto and four Honorarnoten, not the extract (F25
   partly back: the citation is right, the listed sources are not).
2. Sentence lookup (§ 14 UGB clause with Mac-Roman mojibake, F39) — 91 s,
   correct (Straube/Krejci commentaries).
3. Title summary "GA W-10059I(16)_50158" — 107 s, plausible (vehicle
   appraisal), sources padded with MANZ `.MTT` form texts (F5).

Chat, hand-crafted:

4. "Worum geht es in der Gewährleistungsklage von Dipl.Ing. Lisa Klaban gegen
   die Autohaus Lehr GmbH (FN 33950b) vor dem Landesgericht Krems, welches
   Fahrzeug ist betroffen und welcher Betrag wird eingeklagt?" — 187 s,
   **correct** (EUR 87.000, Land Rover Defender, parties and court right),
   all ten sources from KLABAN-LR.
5. "Welche Mängel wurden bei der Begehung des Hauses 1 in der Blumengasse 13,
   Wulkaprodersdorf, am 18.11.2022 festgestellt, und welche davon waren laut
   Status vom 19.12.2022 noch offen?" — 129 s, **honest miss**: the right
   document (Beilage A, Mängelliste, a Markdown table) is the top source, but
   the answer says the list "erscheint nur als Dateiname/Titel" and lists
   defects from a court filing instead. The table rows are chunked without
   the sheet's heading context (F16-type form/table chunks), so the chunks
   that carry "Heizpatrone" etc. were not among the passed ones.
6. "Welche Positionen enthält die 1. Teilrechnung U027/17 der
   Bau-Gut-Schnell GmbH für die Sanierungsarbeiten Elisabethallee 20G in
   1130 Wien?" — 70 s, **correct**: five positions with the right amounts
   (1.166,66 / 999,99 / 666,66 / 333,33), date and total from Beilage N.

Latency 70–187 s (59–127 s last tick), 12–21 k prompt tokens (F11).

New: F38 (worker RSS growth), F39 (Mac Roman mojibake), F40 (embedder rejects
oversized chunks by its own count; root cause of F27); F35 (OCR text of
picture-classified scans dropped, found by the parent session's addendum)
verified and quantified here. Recurring F1 (kills
verified), F5, F7, F11, F12, F13, F14, F16, F18, F24, F25, F27, F28, F30.
#### Tick 22 addendum — 03:00–03:15 UTC, second pass on the same window

Per-file stats since tick 21 (`ingestion_stats`, 2,040 rows): 796 successes
(p50 38 s, p90 602 s, p99 1,379 s; the slowest `AVZ ACC_19115.pdf` 1,785 s,
381 chunks), 1,084 `image_only_pdf`, 160 `no_chunks`, 106 duplicates; 399
pure scans OCRed successfully, so 27 % of the scans now yield text (10 % at
tick 21, F35). The job status still shows `image_only_pdfs_ocr: 0` (F37)
and `failed_files` 298 against 1,568 files without text. 18 conversions hit
the 1,800 s budget (table-heavy PDFs, F43); their workers were killed and
replaced within a second, the files wait for a later round with the same
budget. 16 conversion processes at 3.6–5.0 GB RSS each (F38).

Embedder, second measurement: `docker stats` 99.7–100 % CPU and 9.5 GB; six
1-s `nvidia-smi` samples showed 0 % GPU utilization (3.9 GB allocated),
while the main section's direct 2,283-token request saw 28 % — short
requests are not GPU-bound, they queue: a 15-token request took 17–34 s
three times in a row, and the window's 4,274 sequences per 10 min (p50 287
tokens) ≈ 2.0 k tok/s match the chunk rate of 5.8/s, so ingestion is
embedder-bound and every query embedding waits in the same queue (F18, F29).
Vectors stay consistent (same sentence twice: cosine 0.9999, a different
sentence 0.73). `embedder-query` / `QUERY_EMBEDDING_URL` (PR #29) is not in
Verify's compose.

ETA (F42): the queue is sorted by type priority, so only PDFs have run so
far; `.pdf` 10.4/min from its own window, the other 25 extensions on the
"overall" (= PDF) pace: 14.1 d now, 5.2 d at tick 21.

Chat, hand-crafted (continued):

7. "Im Verfahren 7 C 34/20 d vor dem Bezirksgericht Floridsdorf: Wer sind
   die Parteien, wogegen richtet sich der Einspruch, und welche Beträge
   werden außer Streit gestellt?" (auto) — 58 s, **parties and Einspruch
   right, amount wrong**: the identifier path brought seven HahnFr_VU
   documents; the answer names Friedrich Hahn against Herbert Gschwindl
   Urlaub und Reisen-GmbH and the Niederösterreichische Versicherungs AG and
   the Einspruch against the Zahlungsbefehl of 03.06.2020, but reports the
   claim of € 5.875,18 s.A. as "außer Streit", while the Einspruch puts only
   the vehicle damage of € 3.966,67 and the € 70,00 Unkostenpauschale out of
   dispute (F20); the two companies are "wohnhaft".
8. "Was wirft die AdSimple GmbH in der Abmahnung vom 4.12.2020 Nina
   Grillitsch (Feno) vor, und welche Forderungen werden mit welchen Fristen
   gestellt?" (auto) — 74 s, **correct and complete**: the removed source
   link of a generated Datenschutzerklärung on www.smart-mask.at, the three
   demands with 7/7/14-day deadlines, EUR 850 = 600 + 250, confidentiality,
   plus the 18.12.2020 payment deadline from the Unterlassungserklärung;
   nine of ten sources from the Grillitsch matter in three folders.
9. "Was hat schauinsland-reisen (Funexpress) am 24. April 2020 auf die
   Anfrage von Karin Pozlovsky zur Mauritius-Reise geantwortet?" (fast) —
   45 s (27 s of it waiting for the query embedding), **correct**: no
   booking under her name, send the whole thread, Vorgangsnummer and
   Reiseveranstalter needed, answered by Marco Witzke; seven of ten sources
   are chunks of that one document (F15).

The scripted title summary of this loop's own run ("DZ, Beilage-3", a
lawyer's letterhead page in `SchaNi_SturSa`) was **confabulated**: the
document was not retrieved and the answer summarises APMEA-JUST documents as
if they were it (F5, F20).

New (addendum): F41 (broken-font PDF stored as garbage script), F42 (ETA
extrapolates the PDF pace to .doc/.msg), F43 (timed-out PDFs retried with
the same budget). Recurring in the addendum: F10, F15, F18, F20, F29, F35,
F36, F37.
### Tick 23 — 2026-10-27 14:52 UTC (window 02:50 → 14:52, 12.0 h)

Three cron firings (05:23, 08:23, 11:23 UTC) were queued while the session
was busy; this one tick covers the whole 12 h window.

Job (15.6 h on the new image): 6,718 of 304,723 files (2.2 %), +3,731 in
12 h = 5.2 files/min (14.9 at tick 22, 45.6 at tick 21), per-extension ETA
27.6 d (14.1 d) with no window pace left at all — every extension on the
overall 7.3/min (F42); still only PDFs (3,563 new documents, all `.pdf`).
Chunks 265,665 (+192,891, 4.5/s). Database totals 100,863 documents /
1,259,436 chunks; 358 new documents over 100k chars (commentaries,
yearbooks); 0 duplicate groups; 12 of 300 sampled documents suspect OCR;
2,242 chunks under 20 tokens (F16), 8,667 over 512 (F10). Containers:
backend 1,005–1,295 % CPU / 54 GB (largest conversion process 6.75 GB, F38),
embedder 100 % CPU / 9.7 GB, MongoDB 18.7 GB. No container restarted since
23:18 UTC (the embedder since 22:30).

Embedder (F29, now the whole story): throughput fell from 2.0 k tok/s at
tick 22 to 1.07 k tok/s (2,560 sequences in 10 min, ~14.5 k per hour since
11:00); a 15-token request timed out at 120 s and then took 65 s; GPU
2–42 %. The backend logged 13 "Query embedding failed (APITimeoutError)"
retries, 6 search request timeouts (30 s), 3 HTTP 504 on search and 5
`GET /health` timeouts (F7). Consequence: the sixth and longest blackout of
search and chat (F18).

Per-file stats (12 h, `ingestion_stats`): 3,558 successes (p50 90 s, p90
276 s, p99 1,464 s), 115 `no_chunks`, 34 `image_only_pdf`, 728 pure scans
OCRed — the stretch of tiny junk scans is over, 96 % of this window's scans
yield text (27 % at tick 22, F35) — 12 duplicates, 7 `error` (`[Error: Could
not read file …]` recorded instead of stored, F26 verified). 48 files hit
the 1,800 s budget, but only 3 conversions were killed: 46 of 49 had already
logged "Successfully converted" (`Weitzer-Parkett-Preisliste-2012.pdf`:
Docling finished after 1,168 s, the budget expired 10 min later while the
chunks waited for the embedder) — F44. Nothing partial reached the database
(0 of the 49 stored, 0 chunk-count mismatches among the 3,584 new documents).

Retrieval probes (12): hybrid 3 answered / 9 errors, semantic 5 / 7, median
30.0 s = the request timeout; 0 of 24 by the old measure, the sixth blackout
(F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — **HTTP 504 after 300 s** (F18).
2. Sentence lookup (VTE-Prophylaxe guideline) — 280 s, 2 iterations, 0
   sources, "keine Informationen gefunden": the retrieval behind it timed
   out, so the honest refusal hides an outage (F18).
3. Title summary "Rechnungen WITT" — 145 s, 0 sources, "keine relevanten
   Informationen"; the German no-result answer embeds an English sentence
   ("The user wants a summary of the document …", F45).

Chat, hand-crafted:

4. "Laut der Krankengeschichte des AUVA Traumazentrums Wien (Standort Lorenz
   Böhler) zu Heike Faulhaber: Wann und wo ereignete sich der Unfall, wie kam
   es dazu, und welche Diagnosen wurden gestellt?" (auto) — 297 s, 0 sources,
   **no-result fallback**, although `FaulHe_VU/Unterlagen/Faulhaber KG Lorenz
   Böhler.pdf` (ingested 14:03 UTC, 35 chunks: 12.03.2021 16:00, 1020 Wien,
   bicycle fall, scaphoid fracture left) is in the index; the fallback again
   embeds the English query plan ("The user is looking for specific medical
   and accident details …", F45).
5. "Wer klagt wen in der Klage vom 23.11.2022 an das Handelsgericht Wien
   betreffend die Liegenschaft Strohmayergasse 5, 1060 Wien, worum geht es,
   und wie hoch ist der Streitwert?" (fast) — 147 s, 0 sources, refusal
   that cites a placeholder as a source (`[Source: "Keine relevanten
   Informationen gefunden"]`, F45), although `FP-STROH-BAUM/Gericht/ON 5
   Bestellung des SV/2022-11-23-Klage.pdf` (ingested 13:00 UTC:
   Eigentümergemeinschaft EZ 942 KG Mariahilf against Ing. Hans Drascher
   GmbH, EUR 84.935,33) is indexed. Both questions are answerable; both
   failed on the query embedding (F18, F29).

New: F44 (the 1,800 s budget expires after a successful conversion while the
chunks wait for the embedder; the converted document is discarded and
re-queued), F45 (English leaks into the German no-result answer). Recurring
F7, F10, F11, F12, F13, F16, F18, F29, F35, F36, F38, F42, F43; F26 verified
fixed. Not measurable this tick: F5, F20, F25 (retrieval was down).

#### Tick 23 addendum — 15:20–15:45 UTC, second pass on the same window (fork session)

Embedder: three direct 10-token requests to `127.0.0.1:11090/v1/embeddings`
all hit the 120 s client timeout at 15:1x UTC; the backend logged 13 query
embedding retries (`APITimeoutError`) in the same minutes, 31 of the 79
search requests in the window ended 504 and 3 ended 500. Verify's compose has
neither the `embedder-query` service nor `QUERY_EMBEDDING_URL`, so the PR #29
relief for F29 is merged but not running.

Timeouts (F44, same count): 45 of the 48 files that hit the 1,800 s budget
had logged "Successfully converted" first (`Zivilprozessrecht 2011.pdf`,
`Umwelthaftung 2019.pdf`, EU treaty, UGB and UWG commentaries, ...); only 3
conversions were killed. Each of the 45 will be converted again on its retry
with the same budget (F43).

Encrypted PDFs (F46): the 7 `error` records are password-protected PDFs
(`/Encrypt`, Standard handler), recorded with the generic placeholder text.

Chat, hand-crafted (fork):

6. "Wer sind die Parteien im bedingten Zahlungsbefehl 20 Cg 116/20z vom
   9. September 2020 gegen die Winrooms Betriebs GmbH, welcher Betrag wird
   gefordert und wie setzen sich die Kosten von 2.456,80 EUR zusammen?"
   (auto) — 282 s, 0 sources, **no-result fallback with the English query
   plan** (F45), although `Winroo_Tanzwerk/Gericht/2020-09-14 bedingter
   Zahlungsbefehl Winrooms.pdf` (13:29 UTC; Verein Tanzwerk Showdance vs
   Winrooms Betriebs GmbH, EUR 37.688,00, Kosten 413,70 + 413,70 + 4,10 +
   166,30 + 1.459,00) is indexed. Retrieval timed out (F18, F29).
7. "Was hat ZT DI Marius Johannik am 11. Februar 2022 per E-Mail zum
   Beschauprotokoll der Baugrubensicherung Gobergasse 17 mitgeteilt, und
   welche Abweichungen wurden bei der Beschau festgestellt?" (auto) — 92 s,
   **correct**: deviations of the pile positions found by the surveyor,
   coordination with Dr. Pflüger before continuing, extra bored piles on the
   street side and second neighbour boundary without permit; eight of ten
   sources from APMEA-ACT (the e-mail in three copies, F4/F15). The one
   question whose query embedding got through.
8. "Welchen Betrag fordert das Evangelische Krankenhaus Wien im
   Rückstandsausweis vom 26.11.2018 von Monika Schlinger für den Aufenthalt
   von Patrick Schlinger, und wofür wurde er verrechnet?" (auto) — **HTTP 504
   after 300 s** (F18).

The two loop sessions fired on the same window again (the original session
and this fork share the cron, the state file and this log); from this tick
the fork writes `asks-NN-fork.stdout` and an addendum, as agreed.

New (addendum): F46 (encrypted PDFs reported as unreadable). Recurring in
the addendum: F4, F15, F18, F29, F43, F44, F45.
### Tick 24 — 2026-10-27 17:50 UTC (window 14:52 → 17:50, 3.0 h)

Job (18.5 h on the new image): 7,170 of 304,723 files (2.35 %), +452 in the
window = 2.5 files/min (5.2 at tick 23), per-extension ETA 30.9 d (27.6 d),
still no window pace for any extension (F42) and still only PDFs (439 new
documents, 50 of them over 100k chars). Chunks 288,663 (+22,998, 2.1/s).
Database totals 101,302 documents / 1,282,434 chunks; 0 duplicate groups; 11
of 300 suspect OCR; 318 chunks under 20 tokens (F16), 947 over 512 (F10,
F36). Containers: backend 1,223 % CPU / 52.7 GB (largest conversion
processes 4.3–4.7 GB, F38), embedder 100 % CPU / 9.7 GB, MongoDB 19 GB. No
container restarted (backend since 2026-10-26 23:18 UTC, embedder since
22:30 UTC).

Embedder (F29): 763 tok/s now (1.07 k at tick 23, 2.0 k at tick 22), the
hourly sequence count falling 12.6 k → 11.5 k → 9.9 k, a 15-token request
90 s, GPU 0 %. Backend log (46,399 lines): 170 "Query embedding failed
(APITimeoutError)" retries, 28 search request timeouts, 34 HTTP 5xx on
search, 4 HTTP 504 on chat messages, 7 `GET /health` timeouts (F7), 10 "no active spec" (F24),
41 `TIMEOUT (attempt 1/3)` of which 30 files had already been converted
(F44) and 10 conversions were killed, 13 no-chunk lines. Per-file stats
(`ingestion_stats`, 3 h): 435 successes (p50 151 s, p90 379 s, p99 1,337 s,
21,042 chunks), 14 `no_chunks`, 0 image-only PDFs — the queue is deep in the
large-PDF stretch.

Retrieval probes (12): 0 of 24, every request at the 30 s timeout — the
blackout that began at tick 23 is now 15 h old (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 177 s, 0 sources, "keine Unterlagen … vor": an
   honest-sounding refusal that is really the retrieval outage (F18).
2. Title summary "Vollmacht-Nina-bachheimer" — 146 s, 0 sources, no-result
   fallback with the English query plan again (F45).

Chat, hand-crafted:

3. "Wer sind Kläger und Beklagte im Verfahren 6 Cg 37/2015b vor dem
   Landesgericht für ZRS Wien laut der Urkundenvorlage vom 07.07.2015, wie
   hoch ist der Streitwert, und welche Kosten sind im Kostenverzeichnis
   angeführt?" (auto) — 276 s, 0 sources, no-result fallback with the
   English query plan (F45), although `VinzKu_ArzbJa/Urkundenvorlage.PDF`
   (ingested 15:26 UTC: Jasminka Arzberger against Dr. Bajc / Dr. Zach /
   Dr. Teubl (GbR), EUR 35.550,08, Kostenverzeichnis EUR 1.308,60) is
   indexed and carries the case number for the identifier path.
4. "Was regelt § 1096 ABGB laut dem Kommentar von Prader (Manz Wohnrecht,
   MRG 5.15, Stand 1.10.2020)?" (fast) — 129 s, 0 sources, refusal citing
   the placeholder `[Source: "Verfügbare Informationen"]` (F45), although
   the commentary (`Recherchen PT/… /Manz Wohnrecht MRG 5.15, § 1096
   ABGB.pdf`, 311k chars, ingested in the window) is indexed. Both failed on
   the query embedding (F18, F29).

New: none. Recurring F7, F10, F11, F12, F13, F16, F18, F24, F29, F36, F38,
F42, F43, F44, F45. Not measurable while retrieval is down: F5, F20, F25,
F34, F35 (the new scans are OCRed, but nothing can be asked about them).

#### Tick 24 addendum — 18:20–18:40 UTC, second pass on the same window (fork session)

Embedder (F47): two direct 10-token requests took 104.7 s and 100.5 s; the
10-minute sample gave 1,581 sequences / 437 k tokens = 729 tok/s with the GPU
at 0 %. Over the 19 h since its restart the rate fell 2.0 k → 1.07 k → 0.73 k
tok/s while RSS grew to 9.75 GB — the same decay that a restart cured on
2026-10-26. The ETA and the search blackout both follow from it.

Chat, hand-crafted (fork):

5. "Welche Zuschläge auf den Richtwert macht die Vermieterin in ihrer
   Stellungnahme im Schlichtungsverfahren MA50-Schli-1-541959-2025 (Wohnung
   Top 15, Geibelgasse 1) geltend, wie hoch war der Hauptmietzins und um wie
   viel übersteigt er den zulässigen Richtwertmietzins?" (auto) — **HTTP 504
   after 300 s** (F18); the document (`FP-ENGL-KLÄGER/Attachements/02
   Stellungnahme Anna-Sophie Engl.pdf`, 14:58 UTC: EUR 641,77 net, 39.5 %
   of surcharges, about 18.5 % above the Richtwertmietzins) is indexed.
6. "Wer sind die Vertragsparteien des Wohnungseigentumsvertrags über die
   Liegenschaft EZ 26 KG 01512 Unterdöbling, und was befindet sich laut
   Vertrag auf der Liegenschaft?" (auto) — 213 s, **hedged but right**: the
   answer says the parties are "not explicitly listed" and then names Peter
   Tallafuss and Georg Neumann from a Grundbuch decision, lists the units of
   Pfarrwiesengasse 20; the WE-Vertrag itself (which names both parties in
   its first lines) is among the sources but its opening chunk was not the
   one used (F20/F22). Sources from P-60-14 plus a template `WE-Vertrag` of
   another matter (F15).
7. "Was ergaben die Infrarotspektren der Deckbeschichtungen und der
   Grundbeschichtungen der Muster 2.3.1 bis 2.3.6 im Micro-Hoesch Befund
   Teil 4?" (auto) — 220 s, **correct and precise** (polyester cross-linked
   with RE, bands at 1550/815 cm⁻¹, titanium dioxide, phthalate polyester
   with melamine for the primers); four of six sources unrelated fee
   statements (F5).

Two of three hand-crafted questions got their query embedding through this
time (213–220 s each, i.e. ~100 s of it waiting for the embedder).

New (addendum): F47 (embedder throughput decays until restarted). Recurring
in the addendum: F5, F15, F18, F20, F22, F29.
### Tick 25 — 2026-10-27 20:50 UTC (window 17:50 → 20:50, 3.0 h)

Job (21.5 h on the new image): 7,617 of 304,723 files (2.5 %), +447 in the
window = 2.5 files/min, per-extension ETA 33.8 d (30.9 d), still no window
pace (F42), still only PDFs. Chunks 312,295 (+23,632). Database +429
documents / +24,312 chunks (totals 101,724 / 1,306,039); 15 of 300 sampled
documents suspect OCR (F12), 411 chunks under 20 tokens (F16), 1,551 over
512 (F10, F36), 0 duplicate groups. Containers:
backend 1,348 % CPU / 58.1 GB (F38), embedder 100 % CPU / 9.8 GB, MongoDB
19.3 GB. No container restarted (backend since 2026-10-26 23:18 UTC, the
embedder since 22:30 UTC); `QUERY_EMBEDDING_URL` still unset.

Embedder (F29, F47): 956 tok/s in the 10-minute sample (763 at tick 24), the
hourly sequence counts 11.0 k / 11.1 k / 9.1 k, a 15-token request over the
60 s client timeout, GPU 0 %. Backend log (45,987 lines): 217 "Query
embedding failed" retries, 51 search request timeouts, 54 HTTP 5xx on
search, 2 HTTP 504 on chat messages, 7 `GET /health` timeouts (F7), 6 "no
active spec" (F24), 40 `TIMEOUT (attempt 1/3)` of which 22 files were
already converted (F44) and 18 conversions were killed, 21 no-chunk lines,
2 encrypted PDFs recorded as `error` (F46). Per-file stats (3 h): 414
successes (p50 149 s, p90 452 s, p99 1,644 s, 21,732 chunks), 21
`no_chunks`, 2 `error`, 1 duplicate.

Retrieval probes (12): hybrid 8 answered / 4 errors, hit@1 5, hit@5 7,
hit@10 8, median 1.3 s; semantic 9 / 3, hit@5 7, median 1.4 s — the first
probes that got through since tick 22: the embedder queue is intermittent
now, not permanently blocked, so a request that lands between two large
ingestion batches is answered in a second and one that does not waits past
the 30 s timeout (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 103 s, **correct** (Unternehmensberatung,
   Buchhaltung und Handel; ten Firmenbuchabfrage sources) — the first pass
   since tick 22.
2. Sentence lookup — **HTTP 504 after 300 s** (F18).
3. Title summary "BEZINA Barbara MA Strafkenntnis vom 2021_08_31" — 288 s,
   0 sources, no-result fallback with the English query plan (F45).

Chat, hand-crafted:

4. "Welche Verletzungen erlitt Ingrid Krauss beim Schiunfall vom 25.03.2018
   laut dem unfallchirurgischen Gutachten von Dr. Günther Straub vom
   05.06.2019, und wer hat das Gutachten beauftragt?" (auto) — **HTTP 504
   after 352 s** (F18); the Gutachten (`KrauIn_LansEr/Unterlagen/Krauss
   SV-GA.pdf`, 19:26 UTC: Mittelhandknochenbruch 5 links, Schädel-,
   Brustkorb- und Ellbogenprellung, later rib fractures 3/5/6, mild CRPS,
   commissioned by the Gothaer Versicherung) is indexed.
5. "Worüber beschwert sich Dr. Charlotte Böhm im Schreiben vom 14.11.2014
   an den Vorstand der Siemens AG Österreich im Namen von Harald Goldmann,
   und auf welche Bestimmungen des Gleichbehandlungsgesetzes stützt sie
   sich?" (fast) — 37 s, **correct**: two years of harassment and
   discrimination by two named colleagues because of his registered
   partnership with a man, §§ 17 iVm 21 GlBG (the paragraphs cited from the
   firm's follow-up letter of 02.03.2015 rather than from the letter itself,
   which names § 17 Abs 1 Z 6 and § 21 Abs 1); the AK letter appears three
   times among the sources (F15) and five of ten sources are unrelated (a
   notary handbook, ecolex articles, F5).

New: none. Recurring F7, F10, F11, F12, F13, F16, F18, F24, F29, F36, F38,
F42, F43, F44, F45, F47. Not measurable while retrieval is down: F5, F20,
F25, F34, F35.

#### Tick 25 addendum — 21:10–23:50 UTC, second pass on the same window (fork session)

Embedder: a direct 10-token request took 69.8 s (104 s at tick 24), the
10-minute sample 949 tok/s, GPU 0 % (F47). Two of the three `error` records
of the window are not encrypted but corrupt: `ReisGe2_V-NÜRN/Unterlagen/
Vorbereitender Schriftsatz Schoenherr.pdf` (3.1 MB, `%PDF-1.7`, pdfium "Data
format error") and `Gelöscht/WeberDr_Straf/Dr_ Erwin Weber, Unterlagen 18
Seiten.pdf` (3.2 MB, not a PDF at all: the first bytes are binary noise) —
recorded with the generic "Could not read file" text and a full traceback at
ERROR level each (F26 records them, F46's message problem applies).

Chat, hand-crafted (fork):

6. "Wer sind die klagenden Parteien und die beklagte Partei im Verfahren
   11 Cg 45/15 vor dem Landesgericht Korneuburg (Streitverkündung zur
   Klage), und durch wen werden sie jeweils vertreten?" (auto) — **HTTP 504
   after 300 s** (F18); the document (`NiedWiNIV_WillCo/Streitverkündung,
   Klage.pdf`, 20:47 UTC: Cornelis Willemse and the minors Florian and
   Matthias Nemeth v. Prim. Dr. Christa Freibauer, Dr. Helmut Platzgummer /
   Kuhn Rechtsanwälte) is indexed.
7. "Wer klagt wen im Verfahren 62 Cg 27/2020a vor dem Landesgericht für ZRS
   Wien, und wer vertritt den Erstbeklagten laut der Folgeeingabe vom
   13.10.2020?" (auto) — 273 s, **honest miss** (F48): the ERV form stores
   the number as "062 CG 27/2020a", the identifier path found nothing, and
   the answer lists three other LG ZRS Wien case numbers it did find
   (62 Cg 19/20z, 62 Cg 36/15d, 53 Cg 91/13m) and says none matches —
   correct behaviour on a retrieval miss (no confabulation), sources MANZ
   form texts and unrelated filings (F5). The document (Dr. Daniel Andrysik
   and Dr. Paulina Andrysik-Michalska v. Prim. Univ. Doz. Dr. Manfred
   Mühlbauer, represented by Dr. Stephan Trautmann) is indexed.

New (addendum): F48 (ERV case-number spelling not normalised). Recurring in
the addendum: F5, F18, F26, F46, F47.
### Tick 26 — 2026-10-27 23:50 UTC (window 20:51 → 23:50, 3.0 h)

Job (24.5 h on the new image): 7,993 of 304,723 files (2.6 %), +376 in the
window = 2.1 files/min, per-extension ETA 36.8 d (33.8 d), still no window
pace (F42), still only PDFs. Chunks 332,899 (+20,604). Database +360 documents / +20,449 chunks (totals
102,084 / 1,326,488), 14 of 300 sampled documents suspect OCR (F12), 275
chunks under 20 tokens (F16), 892 over 512 (F10, F36), 0 duplicate groups.
Containers: backend 1,009 % CPU / 47.8–52.3 GB (F38), embedder 100 % CPU /
9.8 GB, MongoDB 19.3 GB. No container restarted (backend since 2026-10-26
23:18 UTC, the embedder since 22:30 UTC); `QUERY_EMBEDDING_URL` still unset.

Embedder (F29, F47): 720 tok/s in the 10-minute sample (956 at tick 25), the
hourly sequence counts 10.4 k / 9.9 k / 8.1 k, a 15-token request over the
60 s client timeout, GPU 0–100 % in single samples. Backend log (45,922
lines): 106 "Query embedding failed" retries, 16 search request timeouts,
22 HTTP 5xx on search, 3 HTTP 504 on chat messages, 6 agent worker tasks
"execution failed: Request timed out", 5 `GET /health` timeouts (F7), 11
search rate-limit hits (F14), 6 "no active spec" (F24), 43 `TIMEOUT
(attempt 1/3)` of which 23 files were already converted (F44) and 20
conversions were killed, 15 no-chunk lines, 1 unreadable PDF recorded as
`error` (F26/F46). Per-file stats (3 h): 354 successes (p50 184 s, p90
491 s, p99 1,190 s, 19,125 chunks), 15 `no_chunks`, 1 `error`.

Retrieval probes (12): hybrid 1 answered / 11 errors, semantic 0 / 12,
median 30.0 s = the request timeout; after tick 25's brief window the
blackout is back (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 277 s, 0 sources, "keine Unterlagen … vor": the
   retrieval outage as a refusal (F18).
2. Sentence lookup — **HTTP 504 after 300 s** (F18).
3. Title summary "Anhang 6-Folienaufstellungen" — 144 s, 0 sources,
   no-result fallback (F45).

Chat, hand-crafted:

4. "Worum geht es in der Rechtssache 9 C 191/20a vor dem Bezirksgericht
   Leopoldstadt, wer sind die Parteien, und wann findet die vorbereitende
   Tagsatzung statt?" (auto) — 272 s, 0 sources, no-result fallback with
   the English query plan (F45), although the Ladung
   (`18-21/Attachements/Bezirksgericht Leopoldstadt 9 C 191 20a - 8.pdf`,
   20:52 UTC: Ernestina u. Tibor Steiner GmbH against Bernhard Wolf,
   Mietzins- und Räumungsklage, Tagsatzung 6 July 2021 10:00) is indexed
   and carries the case number for the identifier path.
5. "Welches Gewerbe bescheinigt der Gewerbeschein des Magistratischen
   Bezirksamts für den 3. Bezirk für Klaus Propst, und mit welcher
   Beschränkung?" (fast) — 126 s, 0 sources, refusal citing the placeholder
   `[Source: "Keine relevanten Informationen gefunden"]` (F45), although
   `CZ-758C/webErv/Beilage .2.pdf` (21:47 UTC: Handelsgewerbe § 103 Abs 1
   lit b Z 25 GewO 1973, beschränkt auf den Handel mit Büromaschinen …) is
   indexed. Both failed on the query embedding (F18, F29).

New: none. Recurring F7, F10, F11, F12, F13, F14, F16, F18, F24, F29, F36,
F38, F42, F43, F44, F45, F47. Not measurable while retrieval is down: F5,
F20, F25, F34, F35, F48.

#### Tick 26 addendum — 00:05–00:25 UTC (2026-10-28), second pass on the same window (fork session)

Embedder at 23:50 UTC: 703 tok/s in the 10-minute sample (949 three hours
earlier), GPU 0 %, RSS 9.8 GB (F47); 11 search requests answered 429 while
both loop sessions probed at once (F14), 15 answered 504, 104 query embedding
retries. 42 budget timeouts, 23 of them after a successful conversion (F44),
19 conversions killed.

Chat, hand-crafted (fork):

6. "Wann fand die Rücknahme der Wohnung Top 27 in der Müllnergasse 26, 1090
   Wien, von DDr. Gunther Tischler statt, wer vertrat den Vermieter und wie
   hoch war die Kaution laut Rücknahmeprotokoll?" (auto) — 274 s, 0 sources,
   **no-result fallback with the English query plan** (F45), although
   `FP-ROHR-TISCH/Attachements/260116 bk - Rücknahmeprotokoll Top 27.pdf`
   (21:42 UTC: 16.01.2026 09:00, Bettina Kloiber for HV Rohr and Dr.
   Fischerlehner, Kautionskonto EUR 4.800,00) is indexed; retrieval timed
   out (F18, F29).
7. "Wie werden die Teilleistungen der Planungsleistung laut § 11 der
   Honorarvereinbarung (Beilage 10 - Honorar Planung im Akt APMEA-JUST)
   prozentuell bewertet, etwa Vorentwurf, Kostenermittlungsgrundlagen und
   Oberleitung?" (auto) — **HTTP 504 after 300 s** (F18). The document's
   percentages are partly OCR-garbled ("Mt ER" for the numbers of three
   items, F12), so even a successful retrieval would have answered only
   Vorentwurf 13 %, Kostenermittlungsgrundlagen 12 %, künstlerische and
   geschäftliche Oberleitung 5 % each.

Both fork questions failed on the query embedding; nothing new. Recurring in
the addendum: F12, F14, F18, F29, F44, F45, F47.
### Tick 27 — 2026-10-28 02:50 UTC (window 23:51 → 02:50, 3.0 h)

**Backend restarted at 00:30 UTC** (not by the loop): an operator halved the
conversion concurrency to 8/8 because the host ran at 100 % CPU / 90 W with
cores at 92–94 °C (F49, F7) and restarted the backend so the job would read
it. Same image (`c475781`), same 512-token chunk budget (F36,
`HybridChunker initialized (max_tokens=512)`), `QUERY_EMBEDDING_URL` still
unset, the embedder not restarted. The job was marked interrupted and
resumed: discovery 6 min, queue 297,198 files, 8 conversion processes; the
resume skipped every known failure (F23 verified).

Job (2.3 h since the resume): 2,314 files at 17.5 files/min — inflated by
1,972 copies skipped in 0.2 s each (the deferred copies of earlier rounds
meeting their already-stored originals, F4 working) — per-extension ETA
32.2 d with a `.pdf` window pace of 4.2/min again (F42). Counters re-based
on the earlier runs: image-only 1,540, no-chunks 552, errors 10. Database
+113 documents / +17,581 chunks (totals 102,197 / 1,344,069), 3 of 111
sampled documents suspect OCR (F12), 158 chunks under 20 tokens (F16), 516
over 512 (F10, F36), 0 duplicate groups. Containers: backend 1,186–1,523 %
CPU / 32–37 GB (fresh process, 8 conversion workers), embedder
100 % CPU / 9.8 GB, MongoDB 19.8 GB.

Embedder (F29, F47): 1,420 tok/s in the 10-minute sample (720 at tick 26),
hourly sequences 4.4 k / 7.6 k / 17.7 k from 00:00 to 02:00 UTC — the
throughput doubled once the host CPU dropped to 74 %, without an embedder
restart; a 15-token request still exceeds the 60 s client timeout. Backend
log (39,777 lines, both containers): 216 "Query embedding failed" retries,
50 search request timeouts, 54 HTTP 5xx on search, 2 HTTP 504 on chat, 0
`GET /health` timeouts after the restart (F7 relieved by 8 slots), 8 "no
active spec" (F24), 63 `TIMEOUT (attempt 1/3)` of which 19 files were
already converted (F44) and 41 kills (16 of them the graceful shutdown),
326 no-chunk lines. Per-file stats (3 h): 106 real successes (p50 310 s
without the duplicates, 16,851 chunks), 236 `image_only_pdf` (new small
scans the resumed queue reached first), 90 `no_chunks`, 1,972 duplicates.

Retrieval probes (12): hybrid 1 answered / 11 errors, semantic 2 / 10,
median 30.0 s = the request timeout (F13, F18) — the 30 s search budget is
still shorter than the embedder queue, while the chat's 300 s budget with
query-embedding retries now gets two of three requests through.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 109 s, **correct**, ten Firmenbuchabfrage
   sources (F25 fix holds when retrieval gets through).
2. Sentence lookup ("Periimplantäre Entzündungen …") — 229 s, **correct**
   document (`Riha_Huber/Riha Huber Dr Gredler Gutachten.pdf`) and a fair
   summary; three of five sources other dental expert opinions.
3. Title summary "Mietrecht. Das Casebook, 11. Kapitel; Tierhaltung" —
   **HTTP 504 after 300 s** (F18).

Chat, hand-crafted:

4. "Welche Fragen soll der gerichtlich bestellte Sachverständige Dr. Arthur
   Marczell im Verfahren 65 Cg 1/19y (Ing. Herbert Maurer gegen Dr. Richard
   Maier) laut seinem Gutachten vom 28.6.2019 beantworten, und wie hoch ist
   der Streitwert?" (auto) — 77 s, **half right, honest about the rest**:
   Streitwert EUR 45.691,94, parties and representatives correct (from a
   Schriftsatz), the questions "not in the fragments" — the Gutachten itself
   (`MaieRi_MaurHe/Gutachten/Gutachten.pdf`, 00:07 UTC, which lists them)
   was not among the ten same-matter sources: it prints the case number as
   "65 Cg1/19y-19" without the space, so the identifier path's exact phrase
   misses it (F48, spacing variant) and the matter's Protokolle and
   Beschlüsse crowd it out (F25-type). No confabulation.
5. "Wer sind Vermieter und Mieter im Mietvertrag für die Wohnung
   Müllnergasse 26 Tür 33, 1090 Wien, und wie lange läuft der befristete
   Vertrag?" (fast) — 126 s, 0 sources, refusal citing the placeholder
   `[Source: "Keine relevanten Informationen gefunden"]` (F45), although
   `FP-ROHR-GEBET/Beilagen/klagende Partei/Beilage .A.pdf` (00:17 UTC:
   Hausinhabung Müllnergasse 26 / Gebäudeverwaltung Josef Rohr GmbH and
   Tobias Gebetsberger, MSc, 10 years from 01.06.2018) is indexed; the
   query embedding timed out (F18, F29).

New: F49 (thermal/power load of the in-API job, concurrency change needs a
restart). Recurring F10, F11, F12, F13, F16, F18, F24, F29, F36, F42, F43,
F44, F45, F47, F48 (spacing variant); F23 verified on the resume. Not
measurable while retrieval is down: F5, F20, F34, F35.

#### Tick 27 addendum — 03:00–03:40 UTC, second pass on the same window (fork session)

Embedder after the backend restart: 1,589–1,599 tok/s in two 10-minute
samples (703 at tick 26), GPU 34 %, container not restarted (28 h up) — the
decay of F47 followed the backend process (host CPU saturation, F49), not
the llama.cpp process; a direct 10-token request still waits 75 s. In the
30 minutes of the fork's questions: 44 query embeddings failed after their
last retry, 40 search requests hit the 30 s timeout, 1 of 11 chat messages
ended 504. Resume accounting: the 236 `image_only_pdf` records written since
00:30 are 236 new paths (none re-recorded, F23 holds); 1,540 image-only and
552 no-chunks records, all distinct paths.

Chat, hand-crafted (fork):

6. "Was trägt das Bezirksgericht Döbling im Verbesserungsauftrag TZ
   5424/2026 vom 27.10.2026 in der Grundbuchssache von Dr. Christoph Pale
   auf, und binnen welcher Frist?" (auto) — 245 s, **honest miss**: "keine
   Informationen zu TZ 5424/2026", six commentary/handbook sources about
   Verbesserungsaufträge in general; the document
   (`BÖHM-KV/webErv/Grundbuch_Verbesserungsauftrag_24884_123970_2026_10_27.pdf`,
   00:39 UTC: die Sterbeurkunde vollständig eingescannt vorzulegen, binnen
   einer Woche, § 82a GBG) is indexed. The Tagebuchzahl is not an identifier
   pattern (F48 extension), and the fuzzy text stage does not reach it.
7. "Wie hoch ist laut dem Mietspiegel Wien 9/2023 der durchschnittliche
   Mietpreis pro Quadratmeter in Wien, und was kostet eine 30-m²-Mietwohnung
   in Wien im Vergleich zu Österreich?" (auto) — 183 s, 0 sources, refusal
   that then recommends visiting the city's and Statistik Austria's
   websites; `Mietrecht/Mietzins/Mietspiegel_Wien_2023.pdf` (00:08 UTC:
   15,41 €/m², 30 m² 20,03 € Wien vs 17,01 € AT) is indexed — query
   embedding timed out (F18, F29).
8. "Was wurde laut der Teileverkauf-Auftragsbestätigung AVAUF087622 vom
   08.11.2017 gekauft, und wie hoch war der Gesamtbetrag inkl. MwSt?"
   (auto) — 293 s, 0 sources, refusal; `BontAn_RaraAd/Teilverkauf-
   Auftragsbestätigung.pdf` (00:04 UTC: 4 KIA Alufelgen 6,5Jx17 + TPMS,
   EUR 2.003,38 inkl. MwSt) is indexed — query embedding timed out. An
   order number like AVAUF087622 is an exact-token case the text stage
   alone would answer if the hybrid endpoint did not require the vector
   stage first (F18).

All three fork questions missed, none confabulated. Recurring in the
addendum: F18, F23 (holds), F29, F45, F47, F48, F49.
### Tick 28 — 2026-10-28 05:50 UTC (window 02:51 → 05:50, 3.0 h)

Job (5.3 h since the 00:30 resume): 2,676 of 297,198 files, +362 in the
window of which 339 were duplicate skips — **8 real documents in 3 h**
(p50 1,085 s each, 6,887 chunks); per-extension ETA 34.9 d, `.pdf` window
pace 1.5/min. The queue is in the literature stretch: 800k–980k-character
commentaries and monographs (Torggler UGB, Riss, Bezemek, Gisch/Reisinger).
Database +13 documents / +10,958 chunks (totals 102,210 / 1,355,027); 0 of
13 sampled documents suspect OCR, 42 chunks under 20 tokens (F16), 370 over
512 (F10, F36), 0 duplicate groups. Containers: backend 1,331 % CPU / 39.5 GB (8 conversion workers), embedder
100 % CPU / 9.9 GB, MongoDB 20.7 GB. No container restarted since 00:30
(the embedder since 2026-10-26 22:30); `QUERY_EMBEDDING_URL` still unset.

Embedder (F29, F47): the relief of the backend restart lasted three hours —
hourly sequences 15.9 k (03:00) → 9.7 k → 8.2 k, 678 tok/s in the 10-minute
sample (1,420 at tick 27), a 15-token request 35 s, GPU 0 %. Backend log
(15,445 lines): 152 "Query embedding failed" retries, 41 search request
timeouts, 49 HTTP 5xx on search, 1 HTTP 504 on chat, 7 `GET /health`
timeouts (F7), 19 "Slow request", 6 agent worker tasks timed out, 7 "no
active spec" (F24).

Budget (F43, F44): 61 `TIMEOUT (attempt 1/3)` in 3 h, 48 of them after a
successful conversion, 12 conversions killed; 39,252 s = 10.9 h of Docling conversion time discarded in the window (e.g. `Die Rechtsnatur der Auftraggeberhaftung …pdf`: converted at 03:32:52 after 8 min, TIMEOUT at 03:44:31 while its chunks queued for the embedder). With only eight
documents stored against 48 converted-and-discarded, the literature stretch
cannot finish under the current budget and embedder — the job is effectively
stalled on it, and every discarded book is re-queued for a retry with the
same budget.

Retrieval probes (12): hybrid 9 answered / 3 errors, hit@1 8, hit@5 9,
median 1.0 s; semantic 12 / 0, hit@5 9, median 1.2 s — the best probe set
since tick 22. The book stretch alternates between 15–20 min in which all
eight slots convert and no chunk reaches the embedder (queries answered in a
second) and bursts of 400–1,260 chunks per finished book (queries time out);
the tick's probes and chats fell into a quiet phase (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 110 s, **correct**, ten Firmenbuchabfrage
   sources.
2. Sentence lookup (footnote 164, Tribunale di Milano) — 268 s, **correct**
   document (`urheber.recht 2, Kucsko_Handig_ § 85-116`, stored in this
   window with 1,071 chunks) and topic; two of five sources an unrelated
   Beilage B (F5).
3. Title summary — 140 s, 10 sources, answered (expected title among the
   sources).

Chat, hand-crafted:

4. "Welche Gesetzesänderungen nennt das Vorwort der 2. Auflage des
   UGB-Kommentars von Torggler (2016) als Grund für die Neuauflage?" (auto)
   — 126 s, **correct** (ZVG, RÄG 2014, GesbR-RG, plus the reception in
   practice and case law), cited to the 794k-character commentary stored
   in this window; 29 k prompt tokens (F11), six of ten sources the
   `Beilage B` copies of FP-3-20 and two copies of an OGH decision (F5,
   F15).
5. "Wer sind die Autoren des Grundrisses 'Versicherungsvertragsrecht'
   (2021), und welche Funktion hat Erwin Gisch?" (fast) — 21 s,
   **correct** (Gisch and Reisinger; Geschäftsführer des Fachverbands der
   Versicherungsmakler), the function attributed to a second book
   (`9783704680211.pdf`) rather than to the asked one — both carry the same
   author note; four of ten sources unrelated (an e-mail, a
   Jahresabrechnung).

New: none. Recurring F7, F10, F11, F13, F16, F18, F24, F29, F36, F42, F43,
F44 (now the pace-setter), F45, F47, and F5 in the hand-crafted answers
(padding sources). Not measurable this tick: F20, F25, F34, F35, F48.

#### Tick 28 addendum — 06:05–06:30 UTC, second pass on the same window (fork session)

Embedder hourly rate since the backend restart (own log, UTC): 02:00 1,033
tok/s, 03:00 1,226, 04:00 785, 05:00 639; 709 tok/s in the fork's 10-minute
sample at 05:55, GPU 0 %, a direct 10-token request 10.7 s in a quiet phase
(F47: the decay returned within three hours of a fresh backend, so neither
"host CPU" nor "backend process age" alone explains it). Budget waste (F44):
the 48 converted-then-discarded files of the window had cost 39,519 s of
conversion (823 s each) — 46 % of the 8 slots' 24 slot-hours.

Chat, hand-crafted (fork):

6. "Was besagt der Rechtssatz RS0021755 (OGH 1 Ob 536/90, verstärkter Senat
   vom 7.3.1990) zum Verhältnis von Gewährleistungs- und
   Schadenersatzansprüchen im Werkvertragsrecht?" (auto) — 144 s,
   **correct** (volle Konkurrenz; Besteller kann auch nach Ablauf der
   Gewährleistungsfrist Schadenersatz verlangen; Kaufvertragsrecht als T2
   Beisatz), cited to the RIS printout stored at 03:58 UTC; five of six
   sources unrelated commentaries and journal articles (F5), 24 k prompt
   tokens (F11).
7. "Wer ist Antragsteller im Sachbeschluss GZ 4 Msch 33/98m vom 28.2.2002
   des Bezirksgerichts Josefstadt, und welche Mieter sind als Antragsgegner
   genannt?" (auto) — 84 s, **correct and complete**: Benediktinerstift
   Melk (Dr. Thomas Pittner) against the tenants of Lederergasse
   23/Florianigasse 40/Laudongasse 33, all twelve named with their Top
   numbers, from the 455k-character OCR of the decision (05:31 UTC); the
   identifier "4 Msch 33/98m" was spelled as printed ("4 Msch 33/98 m" in
   the scan) and still matched. Five of six sources unrelated (F5).

Both fork questions landed in a quiet phase of the embedder and were
answered correctly. Recurring in the addendum: F5, F11, F44, F47.

### Tick 29 — 2026-10-28 08:50 UTC (window 05:50 → 08:50, 3.0 h)

Job (8.3 h since the 00:30 resume): 2,931 of 297,198 files, +255 in the
window of which 242 duplicate skips — **12 real documents in 3 h** (p50
1,039 s each, 9,478 chunks: Fenyves/Kerschner ABGB §§ 1151–1164a and
§§ 1267–1292, Lurger/Melcher, Likar-Griehser, two dissertations, a VTE
guideline), 1,437 PDFs left in the queue; per-extension ETA 37.1 d, `.pdf`
window pace 1.5/min (F42). Database totals 102,222 documents / 1,364,505
chunks; 44 chunks under 20 tokens (F16), 423 over 512 (F10, F36); 1 of 12
sampled documents suspect OCR. Containers: backend 1,176–1,279 % CPU /
35 GB (8 conversion workers), embedder 100 % CPU / 9.9 GB, MongoDB 21 GB.
No container restarted (backend since 00:30 UTC, embedder since 2026-10-26
22:30); `QUERY_EMBEDDING_URL` still unset.

Embedder (F29, F47): hourly 732 → 889 → 786 → 558 tok/s from 05:00 to
08:00 UTC, 830 tok/s in the 10-minute sample, GPU 0 %, a direct 10-token
request 78 s. Backend log (16,557 lines): 25 query embedding retries (2
final failures), 8 × 429 while both loop sessions probed at once (F14),
4 × 500 and 5 × 504 on search, 3 `GET /health` timeouts (F7).

Budget (F43, F44): 57 `TIMEOUT (attempt 1/3)`, 32 of them after a
successful conversion (30,136 s = 8.4 h of conversion discarded, 942 s per
file), 24 conversions killed, and the first two files reached attempt 2/3
— each retry starts the same 1,800 s budget over a 15–20 min conversion.

Retrieval probes (12): hybrid 1 answered / 11 errors, semantic 4 / 8,
median 30.0 s (F13, F18); the chats below went through in 81–210 s.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 81 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup (Mieterinsolvenz) — 88 s, **correct** document
   (Lassingleithner, Die Kaution im Mietrecht, stored in this window) and
   topic; the sources list shows the firm's research memos on the same
   question instead of the dissertation (F5).
3. Title summary "Likar-Griehser - Muster zur GmbH-Gründung" — 103 s,
   **correct** (a forms handbook for GmbH formation), six of ten sources the
   book itself.

Chat, hand-crafted (fork):

4. "Was bezwecken laut dem Musterbuch zur GmbH-Gründung von Likar-Griehser
   die Kapitalerhaltungsvorschriften des § 83 GmbHG, und wann tritt die
   Ausfallshaftung der Mitgesellschafter ein?" (auto) — 122 s, **half
   wrong**: the purpose ("rechnerische Wertbindung des
   Gesellschaftsvermögens") is right, but the trigger of the Ausfallshaftung
   is given as "zum Zeitpunkt der Fälligkeit der Stammeinlage" — the book
   says it arises when the company's assets fall below the Stammkapital
   (Unterbilanz) at the time of the forbidden payment; the answer pasted the
   adjacent sentence about the circle of liable shareholders (F20); 29 k
   prompt tokens (F11); three of six sources other GmbH commentaries.
5. "Wie definiert § 1151 ABGB laut dem Kommentar Fenyves/Kerschner/Vonkilch
   (3. Auflage) den Dienstvertrag im Unterschied zum Werkvertrag?" (auto) —
   210 s, **correct** (Dienstleistung auf gewisse Zeit vs Herstellung eines
   Werkes gegen Entgelt), but the answer calls the Fenyves/Kerschner/Vonkilch
   commentary the "Klang Kommentar" (it is the 3rd edition of Klang's
   commentary, so acceptable) and cites a Schwimann Taschenkommentar and the
   Zeller Kommentar alongside; the asked commentary is not in the sources
   list although it is cited in the text (F5, F15).

New: none. Recurring F5, F7, F10, F11, F13, F14, F16, F18, F20, F29, F36,
F42, F43, F44, F47. Not measurable this tick: F25 (control passed), F34,
F35, F48.
#### Tick 29 addendum — 08:50–09:20 UTC, second pass on the same window (original session)

Own measurements: 895 tok/s in the 10-minute embedder sample at 08:50,
hourly sequences 11.1 k / 9.5 k / 7.0 k (06:00–08:00 UTC), a 15-token
request over the 60 s client timeout, GPU 0 % (F47); 58 budget timeouts, 33
after a successful conversion, 31,503 s = 8.8 h of conversion discarded
(F44); 5 "no active spec" warnings (F24); and one agent request in which a
sub-task that had timed out was reported as a "circular dependency" and its
dependents dropped (F50, new). Own probe run: hybrid 2 answered / 10
errors, semantic 5 / 7; own scripted chats: control 123 s correct (2
iterations), sentence lookup 89 s correct (the Likar-Griehser handbook),
title summary 79 s answered without the requested title among the sources
(F5).

Chat, hand-crafted (original session):

6. "Wer sind die Autoren von 'Muster zur GmbH-Gründung' (4. Auflage 2020),
   bei welcher Kanzlei arbeiten sie, und was sagt das Vorwort über diese
   Auflage?" (auto) — 178 s, 0 sources, German refusal ("keine Daten zum
   Buch … gefunden"): the query embedding timed out (F18, F29) although the
   same book answered the scripted sentence lookup minutes earlier (Mag.
   Arno F. Likar, LL.M. and MMag. Peter Griehser, LIKAR Rechtsanwälte GmbH
   Graz; Vorwort: 4th edition written during the pandemic, Graz July 2020).
7. "Wer sind die Autoren des Leitfadens Arbeitskräfteüberlassung, und für
   welche Unternehmen sind sie tätig?" (fast) — 100 s, 10 sources, **honest
   miss**: the right book (`Arbeitsrecht/9783704670908.pdf`, Johannes Bösze
   MBA of Glorios, Wien, and Mag. Lois Kraft of Bora GmbH, Linz) is among
   the sources, but only its body chunks about Arbeitskräfteüberlassung
   were retrieved, not the title page that names the authors, so the answer
   says the documents contain no author information (F5/F22: title-page
   facts of a 442k-character book are one chunk among 900). No
   confabulation; six of ten sources unrelated handbooks.

New (addendum): F50. Recurring in the addendum: F5, F13, F18, F22, F24,
F29, F44, F47.

### Tick 30 — 2026-10-28 11:50 UTC (window 08:51 → 11:50, 3.0 h)

**Backend and worker recreated at 11:54:22 UTC** (not by the loop): images
`recallhub-backend:load-profiles` / `recallhub-ingestion-worker:load-profiles`
(= `:latest`, label commit `0ce9001`, branch `feature/ingestion-load-profiles`,
not on `main`; previous images tagged `rollback-20261028`). The build adds
ingestion load profiles (`backend/services/ingestion_schedule.py`): the
performance config now reports `load_profile: office_lite`, office hours
08:00–18:00 Europe/Vienna on weekdays with 4 files at once (8 for the plain
office profile), 16 otherwise, applied live — the F49 fix. The status
carries `load_period` and `concurrency_slots`. The operator's 8/8 setting is
superseded (`max_concurrent_files: 16` again outside office hours). The
job was marked interrupted and resumed with a full re-discovery (F23, the
third since the 23:18 deploy); the chunk budget is still 512 tokens (F36),
`QUERY_EMBEDDING_URL` still unset. The tick's three scripted chats ran into
the recreate: `RemoteProtocolError: Server disconnected` after 27 s, 3 ms
and 3 ms — a measurement artefact, not a finding.

Job before the recreate (11.3 h since the 00:30 resume): 3,184 of 297,198
files, +253 in the window of which 44 duplicate skips — **199 real
documents in 3 h** (p50 121 s, 11,815 chunks): the literature stretch is
over, the queue was back on case files (Schriftsätze, Beilagen, a UWG
commentary with 308 chunks), 807 PDFs left; ETA 39.2 d, `.pdf` window pace
1.7/min (F42). Database totals 102,421 documents / 1,376,320 chunks; 12 of
195 sampled documents suspect OCR (F12), 131 chunks under 20 tokens (F16),
712 over 512 (F10, F36). Containers before the recreate: backend
1,182–1,550 % CPU / 38 GB, embedder 100 % CPU / 9.9 GB, MongoDB 21 GB.

Embedder (F29, F47): hourly 679 → 652 → 601 → 448 tok/s from 08:00 to
11:00 UTC, 335 tok/s in the 10-minute sample at 11:50 with the GPU at 85 %
(a long batch in flight), a direct 10-token request 20 s. Backend log
(40,081 lines): 101 query embedding retries (31 final failures), 33 search
requests answered, 5 × 500 and 35 × 504, 1 `GET /health` timeout (F7), 1
encrypted/corrupt PDF recorded (`Gelöscht/EKH_LuggWa/EKH-Lugger
Pflegedokumentation.pdf`, F26/F46). Budget (F43, F44): 36 `TIMEOUT
(attempt 1/3)`, 24 after a successful conversion (21,343 s = 5.9 h of
conversion discarded), 9 conversions killed, 31 final query-embedding
failures.

Retrieval probes (12, before the recreate): hybrid 8 answered / 4 errors,
hit@1 3, hit@5 8, median 1.6 s; semantic 9 / 3, hit@5 6, median 1.1 s
(F13, F18).

Chat, scripted: all three failed on the recreate (see above); the control
question had been answered correctly at ticks 27–29.

Chat, hand-crafted (fork, 11:56–11:58 UTC, against the fresh backend while
it was still discovering, so the embedder was idle):

4. "Worum geht es im Einspruch gegen den bedingten Zahlungsbefehl 32 Cga
   119/21k des Arbeits- und Sozialgerichts Wien (Kerstin Soroczak gegen
   Benno Emanuel Klaghofer), welcher Betrag wird eingeklagt und wie setzt
   sich das Kostenverzeichnis der Klage zusammen?" (auto) — 46 s, **amount
   and parties correct** (EUR 6.629,32 brutto, 8,58 % Zinsen ab 01.08.2021,
   Zahlungsbefehl vom 22.11.2021), but "Das Dokument enthält kein
   detailliertes Kostenverzeichnis" although `KlagBe_SoroKe/Klage.pdf`
   (09:58 UTC) tabulates it (TP2 108,50 / 120 % ES 130,20 / ERV 4,10 /
   USt 48,56 / Pauschalgebühr 335,00 = 626,36): the table rows are a chunk
   that was not passed, and the absence was asserted from the passed ones
   (F22). Three of six sources from an unrelated matter (F5).
5. "Wer klagt wen im Verfahren 18 Cg 45/15t vor dem Landesgericht für ZRS
   Wien laut der Folgeeingabe vom 09.11.2015, und wer vertritt den
   minderjährigen Kläger?" (auto) — 69 s, **correct** (Paul Rainer, born
   04.11.1998, represented by his mother Mag. Dorothe Rainer, against Prof.
   Dr. Dr. Erwin Jonke; dental treatment), all six sources from JonkEr_RainPa.
   The case number was asked in the lawyer's spelling while the ERV form
   prints "018 CG 45/2015t"; a Schriftsatz in the matter spells it as asked,
   so the question did not reproduce F48.

New: none (the F49 fix is deployed as a load profile; verify its effect
next tick). Recurring F5, F7, F10, F12, F13, F16, F18, F22, F23 (third
re-discovery), F26, F29, F36, F42, F43, F44, F47.
#### Tick 30 addendum — 11:50–12:05 UTC, second pass on the same window (original session)

Own measurements before the recreate: 342 tok/s in the 10-minute embedder
sample, hourly sequences 8.3 k / 7.9 k / 6.1 k (09:00–11:00 UTC), a
15-token request 48 s (F47); 36 budget timeouts, 24 after a successful
conversion, 21,353 s discarded (F44); 5 "no active spec" (F24); a second
"circular dependency" (F50). After the recreate, with the job in discovery
and no chunks flowing, the same 15-token request took 1.1 s. Own scripted
chats and the first run of the questions below all died on the recreate
(`Server disconnected`); the questions were re-run at 11:58–12:02 UTC.

Chat, hand-crafted (original session, re-run):

6. "Welche Beträge anerkennt die Schuldnerin in der Ratenvereinbarung mit
   dem Erzbistum Wien vom 22.04.2026 (Objekt Brigittaplatz 17/1/8), wie
   hoch sind die monatlichen Raten, und was passiert bei Zahlungsverzug?"
   (auto) — 111 s, 24 k prompt tokens (F11), **mostly right, one field
   wrong**: total EUR 6.221,21, EUR 529,00 monthly from 01.05.2026, costs
   175,50 and 36,00, the rent still due on the 5th — but the EUR 5.715,16
   is labelled "Verzugszinsen" (it is the offener Rückstand; the 294,55 of
   interest is missing) and the Terminverlust clause is reduced to its cost
   sentence, omitting that the whole balance falls due (F20, table fields
   read across rows); the document appears three times among the sources
   next to other matters' Ratenvereinbarungen (F15, F5).
7. "Wer klagt wen im Verfahren 7 Cg 110/21d vor dem Landesgericht für ZRS
   Wien, wie hoch ist der Streitwert, und was wird der beklagten Partei im
   Beschluss vom 20.10.2021 aufgetragen?" (fast) — 6 s, **wrong**: the
   answer names Hava Ünver against Dr. Bernhard Gisinger & Partner OG and
   Dr. Richard Nemetz with EUR 67.920,00 — another matter (`KrstMi_ÜnveHa`)
   — although three `KlemUl_RistNi` documents of the asked case were among
   the ten sources. The Klage with the number (`KlemUl_RistNi-2/SS
   KP/…Klage___.pdf`, 10:41 UTC: Nikola Ristic against Univ.-Prof. Dr.
   Ulrich Michael Klemen and the Zürich Versicherung, EUR 48.036,21,
   Klagebeantwortung binnen vier Wochen) prints the number as
   "7 Cg 110121d" (OCR, slash lost), so the identifier path cannot pin it
   (F48) and the fast path answered from the wrong matter with full
   confidence (F20, F17).

New (addendum): none. Recurring in the addendum: F5, F11, F15, F17, F20,
F24, F44, F47, F48, F50.

### Tick 31 — 2026-10-28 14:50 UTC (window 11:51 → 14:50, 3.0 h)

Job (2.9 h since the 11:54 recreate): 1,676 of 296,617 files, of which
1,572 duplicate skips from the re-discovery — **14 real documents in 3 h**
(p50 1,282 s each, 8,217 chunks: Gagawczuk/Thamm, Lindmayr
Angestelltengesetz, two Company-Law books, a 1.9M-character
Leistungsverzeichnis), 63 new `image_only_pdf` and 30 `no_chunks` records,
3,658 PDFs in the queue again (the re-discovery re-queued the books that
had timed out); ETA 36.9 d, `.pdf` window pace 7.3/min counting the
duplicate skips (F42). Database totals 102,435 documents / 1,384,537
chunks; 811 of 8,217 new chunks over 512 tokens (F10, F36).

Load profile (F49 fix, deployed 11:54 UTC): `File slots: 4 of 16 (office
hours, load profile office_lite)` at 12:02:55; at 14:19:35 someone saved
the performance config with `load_profile=office` and the slots went to
`8 of 16 … was 4` at 14:20:02 — applied live, no restart. Backend at
100–114 % CPU (one core) with 4 slots, 35.6 GB; embedder 100 % CPU / 9.9 GB;
MongoDB 21.7 GB. Host relief confirmed, throughput cost: 14 documents.

Budget (F43, F44, **F51 new**): 23 `TIMEOUT (attempt 1/3)`, all killed
during conversion at 731–754 s — the budget scaled down with the 4-slot
profile (`conversion_budget_factor` 1 instead of 4), so the table-heavy
files that already failed at 1,800 s now fail four times faster; 0
post-conversion timeouts this window (books of 900 chunks still embedded
within the budget because only 4 files competed for the embedder). 109
conversions finished (avg 159 s).

Embedder (F29, F47): hourly 461 → 0 (recreate) → 465 → 398 tok/s from
11:00 to 14:00 UTC, 1,162 tok/s in the 10-minute sample at 14:50 with the
GPU at 2 %, a direct 10-token request 93.5 s: even at a quarter of the
earlier load a query waits behind the 100-input batches of the four slots
(`-np 4`), which is the mechanism behind F18 regardless of load. Backend
log (23,155 lines): 0 query embedding retries from the loop before the
tick (no search traffic in the window), 1 `GET /health` timeout (F7), 93
no-chunk lines.

Retrieval probes (12): hybrid 3 answered / 9 errors, semantic 2 / 10,
median 30.0 s (F13, F18) — the tick's probes started while the embedder
was busy with a book.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 64 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup (Art. 23 Abs. 3 CMR, 25 Franken je Kilogramm) — 55 s,
   **correct** (the historical CMR text as quoted in the UGB Kommentar I 4),
   three of five sources unrelated commentaries (F5).
3. Title summary "Grundbuch_Beschluss_24888_123989_2026_10_28" — 66 s,
   **honest miss**: "keine spezifische Zusammenfassung … für den exakten
   Titel", then a description of similar Grundbuch extracts; the document
   (12:30 UTC) was not retrieved by its title (F5).

Chat, hand-crafted (fork):

4. "Wogegen richtet sich die Beschwerde von Markus Potocnik im Verfahren
   VGW-001/056/12096/2019 vor dem Verwaltungsgericht Wien, und wer war bei
   der mündlichen Verhandlung am 27.11.2019 anwesend?" (auto) — 94 s,
   **correct and complete** (Straferkenntnis der MA 58 vom 06.08.2019 nach
   dem Wiener Tierhaltegesetz; Muthgasse 62, Saal 5; Potocnik, Dr.
   Trautmann represented by Dr. Fischerlehner, MA 58 and the
   Tierschutzombudsstelle waived, witnesses Bischof, Burgstaller, Thomas,
   Partel), from the protocol stored at 11:51 UTC plus the judgment; three
   of six sources the `Beilage B` copies of FP-3-20 (F5, F15).
5. "Wer ist Nebenintervenient im Verfahren 33 Cg 31/2026f vor dem
   Landesgericht Krems an der Donau (Klaban gegen Autohaus Lehr GmbH), und
   wer vertritt die beklagte Partei laut der Replik vom 28.10.2026?" (auto)
   — 66 s, **correct** (Jaguar Land Rover Austria GmbH, Salzburg, MMag.
   Barbara Haberlander; Noé-Nordberg Mautner Rechtsanwälte OG, Waidhofen an
   der Thaya), all six sources from KLABAN-LR including the Replik stored
   at 12:30 UTC. The ERV-style number "33 CG 31/2026 f" was asked as
   "33 Cg 31/2026f" and matched (F48 not reproduced).

New: F51 (budget shrinks with the load profile). Recurring F5, F7, F10,
F13, F15, F18, F23 (attempt counters reset by the resume), F29, F36, F42,
F43, F47, F49 (fix live, slot changes applied without restart).
#### Tick 31 addendum — 14:50–15:15 UTC, second pass on the same window (original session)

Own measurements: 1,351 tok/s in the 10-minute embedder sample at 14:50
(hourly sequences 4.5 k / 6.1 k for 13:00–14:00 UTC, GPU 2–22 %, one of
four slots busy) while a 15-token request still exceeded the 60 s client
timeout — the same request took 1.1 s at 11:58 with no chunks flowing, so
the latency is queue depth behind the 100-input batches, not throughput
(F47, F18). Of this window's 23 killed conversions, none had timed out at
ticks 28–30, but the Kassabuch reports `ER-Dach_KB_2019.PDF` and
`ER_KB_2019-1.PDF` first timed out on 10-27 00:07 (tick 22) and are now on
their third from-scratch attempt (F23 × F43, F51). 44 chunks under 20
tokens (F16), 1 "no active spec" (F24). Own scripted chats: control 73 s
correct, sentence lookup 72 s correct, title summary 111 s with the
requested title among the sources.

Chat, hand-crafted (original session):

6. "Wer ist der Autor des Kurzkommentars zum Angestelltengesetz, und welche
   Gruppe stellt laut dessen Kurzbeschreibung den größten Teil der
   unselbstständig Erwerbstätigen in Österreich dar?" (auto) — 44 s,
   **correct and complete** (Mag. Manfred Lindmayr; Angestellte, about 1.7
   million, almost every second Dienstverhältnis under the AngG; the work's
   1,000+ Leitsätze), cited to the 1.45M-character commentary stored at
   13:xx UTC in this window; 21 k prompt tokens (F11), eight of ten sources
   unrelated (collective agreements, a Vereinsrecht book, F5).
7. "Welche Hausverwaltung erstellte die Rücklagenabrechnung 2013
   (Langfassung) für die Liegenschaft Geylinggasse 11, 1130 Wien?" (fast)
   — 7 s, **correct** (Immobilienverwaltung Astrid Reisser, Franz-Josefs-Kai
   65), answered from a copy of the Abrechnung inside a 2018 Direktzustellung
   of the same matter rather than from the Falldokument asked about; that
   Direktzustellung fills seven of the ten source slots (F15).

New (addendum): none. Recurring in the addendum: F5, F11, F15, F16, F18,
F23, F24, F43, F47, F51.
### Tick 32 — 2026-10-28 19:20 UTC (window 14:51 → 19:20, 4.5 h)

The 17:50 firing was missed (the session was busy), so this tick covers
4.5 h. Slots: 8 under `office` until 18:00:40 UTC, then `16 of 16 (outside
office hours)` — the night profile applied live (F49). **The backend was
recreated again at 19:21:49 UTC, a minute into this tick** (same image `0ce9001`, only the backend container, 2.5 min after a performance-config save with `load_profile=office`, 16 slots — the reason is not recorded in any session note): the
job was marked interrupted and resumed with a fourth re-discovery (F23); the
three scripted chats ran into the shutdown (`Server disconnected`, 6–10 ms)
and are not counted; the hand-crafted questions were run after the restart.

Job before the recreate (7.4 h since 11:54): 2,447 of 296,617 files, +771
in the window of which 739 duplicate skips — **26 real documents** (p50
1,304 s, 28,755 chunks: Jula "Der GmbH-Gesellschafter", Hanschmann,
Tophof, Forgó/Zöchling-Jud ÖJT-Gutachten, the DSGVO as a 2.8M-character
PDF); 1,968 PDFs left; ETA 39.1 d, `.pdf` window pace 2.4/min (F42).
Database +30 documents / +32,630 chunks (totals 102,465 / 1,417,167); 1 of
30 sampled documents suspect OCR, 72 chunks under 20 tokens (F16), 982
over 512 (F10, F36), 0 duplicate groups. Containers at 19:20: backend
1,610–1,874 % CPU / 75.9–76.6 GB (F38), embedder 0.1–0.3 % CPU / 9.9 GB,
MongoDB 112–120 % CPU / 22 GB.

Embedder (F29, F47): hourly sequences 14.4 k / 17.6 k / 4.9 k / 0.1 k /
4.1 k from 15:00 to 19:00 UTC — busy while 8 slots delivered books, almost
idle from 17:00 while 16 fresh slots converted the next layer of 1M+
character books — 924 tok/s in the 10-minute sample, a 15-token request
1.3 s at 19:20 (queue empty). Backend log (39,828 lines): 98 "Query
embedding failed" retries, 37 search request timeouts, 37 HTTP 5xx, 2
`GET /health` timeouts (F7), 26 "Slow request", 7 "no active spec" (F24);
5 embedder batches rejected as too large and 4 chunks of the
Weitzer-Parkett price list dropped (F40/F41). Budget (F43, F44, F51): 67
`TIMEOUT (attempt 1/3)`, all at `1800s elapsed` (slots 8–16), 18 after a
successful conversion (6.8 h of conversion discarded), 46 conversions
killed.

Retrieval probes (12, before the recreate): hybrid 8 answered / 4 errors,
hit@1 6, hit@5 8, median 0.85 s; semantic 8 / 4, hit@1 6, median 0.37 s —
the embedder was idle (F13, F18).

Chat, scripted: all three failed on the recreate (see above).

Chat, hand-crafted (after the restart, job in discovery):

4. "Wer erstattete das Gutachten 'Das Vertragsrecht des ABGB auf dem
   Prüfstand: Überlegungen im digitalen Zeitalter' für den 20.
   Österreichischen Juristentag 2018, und wer ist der Herausgeber des
   Bandes?" (auto) — 106 s, **correct in substance** (Nikolaus Forgó and
   Brigitta Zöchling-Jud, Band II/1, 2018, Herausgeber Österreichischer
   Juristentag), cited to the 1.2M-character ÖJT volume stored at 17:41 UTC
   — but the answer writes "Forg" for Forgó throughout (the ó dropped by
   the model, the source text has it), and the Herausgeber line is cited
   to a different Zöchling-Jud book instead of the volume itself (F15);
   six of ten sources unrelated (F5).
5. "Wer ist der Autor der dritten Auflage von 'Der GmbH-Gesellschafter',
   und welche Themen nennt das Titelblatt?" (fast) — 13 s, **wrong**:
   Koppensteiner/Rüffler's GmbHG commentary, 3rd edition, is given as the
   answer; the asked book (Rocco Jula, "Der GmbH-Gesellschafter", 3rd
   edition, Springer, 1.05M characters, stored at 16:xx UTC) was not among
   the ten sources — the title was not matched (F5) and the fast path
   answered from the nearest "3. Auflage" GmbH text with full confidence
   (F20).

New: none. Recurring F5, F7, F10, F11, F13, F15, F16, F18, F20, F23, F24,
F29, F36, F38, F40, F41, F42, F43, F44, F47, F49, F51. Not measurable:
F25 (control failed on the recreate), F34, F35, F48.

#### Tick 32 addendum — 19:19–19:40 UTC, second pass on the same window (fork session)

Own measurements before the restart: backend 1,784 % CPU / 76.7 GB RSS
with 16 conversion processes (F38: 35 GB with 8 slots at tick 31, so the
night profile doubles the footprint), MongoDB 171 % CPU, embedder 9 % CPU /
1,488 tok/s in the 10-minute sample, GPU 3 %; 67 budget timeouts at 1,800 s,
18 after a successful conversion (24,602 s = 6.8 h discarded, F44), 46
kills; 98 query-embedding retries (29 final failures), 37 × 504 on search;
performance-config saves at 16:47 (office hours to 19:00) and 19:19 (back to
18:00) UTC, then the `docker restart` at 19:21:47 (SIGTERM, graceful
shutdown in 0.1 s, resume with discovery at 19:22:09). The fork's three
questions also died on the restart and were re-run at 19:23–19:28 UTC
against the fresh backend (job in discovery, embedder idle):

6. "Wer ist die Autorin der Monografie 'Gewerbeausschlussgründe im
   GmbH-Recht' (2023, Band 34), und was ist ihr beruflicher Hintergrund?"
   (auto) — 47 s, **correct** (Mag. iur. rer. oec. Dr. iur. Lisa
   Mayr-Stumpf; Universitätsassistentin at the Institut für Unternehmens-
   und Gesellschaftsrecht, Universität Wien, now Unternehmensjuristin in
   Innsbruck, plus the Gerichtspraktikum and Rechtsanwaltsanwärterin year
   from the author note), cited to the book stored at 17:xx UTC; three of
   six sources unrelated data-protection articles (F5).
7. "Welche Verjährungsfrist galt laut Rünzler (2020) für die Gründerhaftung
   nach Art 213 ADHGB, und warum wich man von der dreißigjährigen Frist
   ab?" (auto) — 133 s, **correct and complete** (Fünfjahresfrist ab
   Eintragung in das Handelsregister; "viel zu lang", Haftung nur erträglich
   bei kurzem Erlöschen, Beweismittel der Haftenden), 23 k prompt tokens
   (F11); four of six sources other GmbH commentaries (F5).
8. "Wer hat 'Prozessidee und Richtermacht' von Peter Böhm (2019) aus dem
   Nachlass herausgegeben, und wie lautet der Untertitel?" (auto) — 35 s,
   **correct** (Ena-Marlis Bajons and Alexander Somek; "Von der Analyse zur
   Synthese in der Grundlagentheorie des zivilgerichtlichen Verfahrens"),
   with Böhm's dates 1943–2012 from the frontispiece; the `Beilage B` copies
   of FP-3-20 again among the sources (F5, F15).

Three of three fork questions correct once the embedder queue was empty —
the quality is there whenever retrieval is not starved (F18, F29).
Recurring in the addendum: F5, F11, F15, F18, F23, F29, F38, F44.
### Tick 33 — 2026-10-28 20:50 UTC (window 19:20 → 20:50, 1.5 h)

A short window: the 20:23 firing came 1.5 h after the delayed tick 32.

Job (1.5 h since the 19:22 resume; discovery 10 min, queue 398,949 →
296,485): 1,359 files of which 1,312 duplicate skips — **4 real documents**
(p50 1,791 s, 1,274 chunks), 25 image-only and 18 no-chunk files, 11
budget timeouts (10 conversions killed, 1 after a finished conversion,
F44/F51); 3,978 PDFs in the queue (1,968 before the recreate: the timed-out
and in-flight books are back, F23); ETA 37.6 d, `.pdf` window pace
21.5/min counting the skips (F42). Slots: `8 of 8 (outside office hours,
load profile office)` since 19:32 although the saved config says
`concurrent_files=16` and the night period gave 16 of 16 before the
recreate — the resume read a ceiling of 8 from somewhere (F52, new; the
night now runs at half the intended rate). Database +4 documents / +1,274 chunks (totals 102,469 /
1,418,441); 624 of the 1,274 new chunks over 512 tokens (F10, F36), 2 of 4
sampled documents suspect OCR (a phone bill's call log, F12). Containers:
backend 1,923–1,937 % CPU / 37 GB (8 slots, Docling threads per model
higher), embedder 0.1–1.1 % CPU (idle: 4 tok/s in the 10-minute sample, 35
sequences; the eight slots are all inside 1M-character books), MongoDB
22 GB.

Backend log (12,576 lines): 0 query-embedding retries, 0 search timeouts,
0 HTTP 5xx, 14 "Slow request", 6 "no active spec" (F24), 43 no-chunk
lines. A 15-token embedding request took 1.3 s.

Retrieval probes (4 sampled, the window held only 4 documents): hybrid 4
answered, hit@5 4, median 0.28 s; semantic 4, hit@5 4, median 0.22 s — the
embedder is idle (F13).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 81 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup (Baugrundstückspreise 2018) — 65 s, **correct**
   document, all sources the same table (F15).
3. Title summary ("Falldokument-Abfrage 30.03.2022 Beilage … aus 5 MSCH
   13_2015 h", the Geylinggasse Rücklagenabrechnung) — 104 s,
   **confabulated**: the document was not retrieved and the answer describes
   "Darlehenskosten sowie Ratenvereinbarungen" from a Teilsachbeschluss and
   two Stundungsanträge of other matters as if they were it (F5, F20).

Chat, hand-crafted:

4. "Was regelt § 84 UrhG laut dem Kommentar Kucsko/Handig, urheber.recht
   (Stand 1.4.2017), und wer ist der Autor dieser Kommentierung?" (auto)
   — 55 s, **correct** (Unterlassungs- und Beseitigungsanspruch in den
   Fällen der §§ 79 und 80; St. Korn; the Zitiervorschlag verbatim), from
   the 1M-character `§ 62-84` file stored at 19:xx UTC; the "additional
   context" attributes the § 78 commentary to Kodek on the strength of an
   OGH decision rather than the commentary (F15-type citation drift); eight
   of ten sources other parts of the same commentary.
5. "Wie hoch ist laut der Tabelle der Baugrundstückspreise 2018 der
   Bezirksdurchschnitt pro Quadratmeter im Bezirk Eisenstadt (Stadt)?"
   (fast) — 7 s, **correct** (221,7 Euro/m²), all ten sources the same
   table (F15).

New: F52 (resumed job runs 8 of 8 outside office hours although the saved
config says 16). Recurring F5, F10, F11, F12, F13, F15, F20, F23, F24,
F36, F42, F43, F44, F49. Not seen this window: F7, F18, F29, F47 (the
embedder was idle), F40/F41.

#### Tick 33 addendum — 20:50–21:15 UTC, second pass on the same window (fork session)

Own measurements: backend 1,871 % CPU / 37.2 GB (8 slots), embedder 0.2 %
CPU with the GPU at 91 % — the GPU time is Ollama's (gemma4:12b answering
the two sessions' chats), not the embedder's; 11 budget timeouts, 1 after a
finished conversion (1,552 s discarded); 24 search requests, all 200. The
four documents of the window are the table-heavy files that had been
killed at 750–1,800 s in earlier rounds (`baugrundstueckspreise_2018.pdf`
233 chunks, `DREI RECHNUNG-1.pdf` 421, its copy, the Geylinggasse
Rücklagenabrechnung 199) — they finished at 1,816–1,864 s, i.e. just over
the old cap, under the 8-slot night budget (F43 relieved for these four,
F51 still true for the day profile).

Chat, hand-crafted (fork):

6. "Wie hoch war laut der Liste der Grundstückspreise 2018 der
   Bezirksdurchschnitt im Bezirk Eisenstadt (Stadt), Burgenland?" (auto) —
   77 s, **wrong value from the neighbouring row**: "143,8 Euro pro
   Quadratmeter" as the Bezirksdurchschnitt, with 221,7 demoted to "die
   Gemeinde Eisenstadt"; 143,8 is the Bezirksdurchschnitt of B.Nr. 102
   Rust (Stadt), the next row of the same table (F20: table rows read
   across). The original session's fast-mode run of the same question
   (question 5 above) returned 221,7 correctly — auto mode's synthesis
   over the wider chunk set lost the row alignment.
7. "Welche Rechnungsnummer und welches Rechnungsdatum hat die
   Drei-Mobilfunkrechnung mit der Detailübersicht für die Rufnummer
   06604764252 (Kundennummer 9280216145)?" (auto) — 39 s, **correct and
   richer than asked** (698554722, 12. Okt. 2015, EUR 578,95 brutto, tariff
   3SuperSIM Superphone L, an older invoice for the same number named),
   every source from AltiNe_HutchiGe — the identifier-like numbers matched
   exactly; the same file the scripted title summary could not find by its
   title "DREI RECHNUNG-1" (F5: title lookup vs content lookup).
8. "Welche Immobilienverwaltung erstellte die Rücklagenabrechnung 2013
   (Langfassung) in der Beilage zum Verfahren 5 Msch 13/2015h, und für
   welche Liegenschaft?" (auto) — 48 s, **correct** (Immobilienverwaltung
   Astrid REISSER; Geylinggasse 11 (WEG), 1130 Wien; BG Hietzing; net
   expenses EUR 12.693,25), cited to a court filing of the matter that
   quotes the same Abrechnung rather than to the Beilage stored in this
   window (F15); the scripted title summary of that Beilage had been
   confabulated minutes earlier (F5, F20).

Two of three fork questions correct, one table-row mix-up (F20). Recurring
in the addendum: F5, F15, F20, F43, F51.
### Tick 34 — 2026-10-28 23:50 UTC (window 20:50 → 23:50, 3.0 h)

**Backend, worker and frontend recreated at 21:37 UTC** (not by the loop):
images `:power-plan` = commit `bbafb3b` on `feature/ingestion-load-profiles`
(PR #30, unmerged; previous build kept as `rollback-20261028b`) — the host
agent is to control a CPU clock cap during office hours (agent 1.1.0 not
yet installed; the manual Windows cap from 16:47 stays), the
performance-config save keeps `timeout_config`, and the live config now has
`timeout_config max 3600`: per-file budgets `x4.0` at 16 night slots, so
timeouts fire at `3600s elapsed` (F43, F51). Fifth re-discovery of the job
(F23): 15.5 min, queue 296,435, slots `16 of 16 (outside office hours)`,
Docling threads per model 1, chunk budget still 512 (F36),
`QUERY_EMBEDDING_URL` still unset, the embedder not restarted.

Job (2.2 h since the 21:38 resume): 1,554 files, of which 1,536 duplicate
skips — **8 real documents** since tick 33 (p50 2,836 s, max 3,334 s,
6,557 chunks: the Kassabuch `ER-Dach_KB_2019.PDF` at last, Lindmayr's
Arbeitszeit and Entgelt handbooks, a Konkurs-Rechnungslegung, the Weitzer
price list); 3,340 PDFs left; ETA 35.9 d, `.pdf` window pace 5.5/min with
the skips (F42). Database +11 documents / +10,383 chunks (totals
102,480 / 1,428,824); 1 of 11 sampled documents suspect OCR, 107 chunks
under 20 tokens (F16), 640 over 512 (F10, F36), 0 duplicate groups.
Containers: backend 1,203 % CPU / 62.7 GB (16
slots, F38), embedder 100 % CPU / 9.9 GB, MongoDB 22 GB.

Embedder (F29, F47): 1,421 tok/s in the 10-minute sample, hourly sequences
3.8 k / 5.2 k / 7.1 k (21:00–23:00 UTC), a 15-token request over the 60 s
client timeout again at 23:50 (1.3 s at tick 33 when the slots were all
converting). Backend log (20,600 lines): 3 "Query embedding failed"
retries, 3 search request timeouts, 3 HTTP 5xx, 3 `GET /health` timeouts
(F7), 5 "Slow request"; 45 embedder rejections: 13 chunks of `P-76-11/Prospekte/Weitzer-Parkett-Preisliste-2012.pdf` dropped after three retries each (`input (9579 tokens) is too large to process`, the price list's table rows tokenising far above their MiniLM count); the rest of the document was stored (F40/F41).
Budget: 13 `TIMEOUT (attempt 1/3)` at
3,600 s, 5 after a successful conversion (14,263 s discarded, F44), 8
conversions killed.

Retrieval probes (12): hybrid 0 answered / 9 errors, semantic 2 / 7,
median 30.0 s = the request timeout — the blackout is back with 16 slots
delivering books to the embedder (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 78 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup — not run (no probe answered).
3. Title summary "Der Verbrauchervertrag, Ullreich (2016)" — 84 s,
   **correct** (a monograph on the Unternehmer- and Verbraucherbegriff),
   the book among the sources but six of ten slots the `Beilage B` copies
   of FP-3-20 (F5, F15).

Chat, hand-crafted:

4. "Worum geht es in der Rechnungslegung im Konkurs der Goldmann Druck AG
   (14 S 103/11d), und welches Konto wird darin abgerechnet?" (auto) —
   74 s, **correct** (Fortführungskonto 307 58 01 0300 (40 630), the
   creditor's position from the firm's Jahresberichte 2019/2020, LG St.
   Pölten, Kosch & Partner), the Rechnungslegung stored in this window;
   seven of ten source slots the same Beilage (F15), 23 k prompt tokens
   (F11).
5. "Worum geht es laut der Kurzbeschreibung im ARD-Handbuch 'Entgelt ohne
   Arbeit' von Lindmayr?" (fast) — 22 s, **honest miss**: the book fills
   eight of ten sources, but the Kurzbeschreibung (its first chunk,
   Entgeltfortzahlungspflicht des Arbeitgebers) was not among the passed
   chunks, so the answer says no description is contained (F22, the
   title-page problem of tick 29 again).

New: none. Recurring F5, F7, F10, F11, F13, F15, F16, F18, F22, F23, F29,
F36, F38, F40, F41, F42, F43, F44, F47, F49. Verified relieved: the
Kassabuch `ER-Dach_KB_2019.PDF` finished under the 3,600 s budget (F43).

#### Tick 34 addendum — 23:50–00:15 UTC (2026-10-29), second pass on the same window (fork session)

Own measurements: backend and worker recreated at 21:37:50 UTC from images
`:power-plan` (= `:latest`, commit `bbafb3b`, branch
`feature/ingestion-load-profiles`: the host agent caps the CPU clock during
office hours, the performance-config save keeps `timeout_config`, previous
build kept as `rollback-20261028b`) — the fifth re-discovery since the
2026-10-26 deploy (1,841 duplicate skips in the window, F23). Night slots
`16 of 16` from 21:53. Budget timeouts now fire at 3,600 s (13 in 3 h, 5
after a finished conversion, 14,261 s discarded, F44) and the Kassabuch
files `ER-Dach_KB_2019.PDF` / `ER_KB_2019-1.PDF` and `PDF-Dokument
Beilage(1).pdf`, killed in every round since 2026-10-27, finally finished
(p50 of the 11 real documents 2,737 s, max 3,334 s — F43 relieved at
night, F51 still true by day). Backend 1,107 % CPU / 63 GB with 16 slots
(F38), embedder 100 % CPU, 468 → 726 tok/s hourly, 1,391 in the 10-minute
sample, GPU 0 %; 3 `GET /health` timeouts (F7); the loop's probes all hit
the 30 s timeout again (F18) while the chats went through in 40–107 s.

Chat, hand-crafted (fork):

6. "Auf welcher wissenschaftlichen Arbeit basiert 'Erbfolge in den
   GmbH-Geschäftsanteil' von Thomas Wolkerstorfer (2020), und an welcher
   Universität ist der Autor tätig?" (auto) — 107 s, **half answered**:
   JKU Linz, Institut für Unternehmensrecht, assoz. Univ.-Prof. correct,
   but the basis is given as "eine wissenschaftliche Arbeit im Rahmen
   seiner Tätigkeit" — the Vorwort (Habilitationsschrift, angenommen im
   Februar 2019) was not among the passed chunks, so the answer stayed
   vague instead of wrong (F22-type); four of six sources other
   inheritance-law texts (F5).
7. "Wer sind die Herausgeber der 'Innsbrucker Schriften zum
   Unternehmensrecht' (Band 9: Ullreich, Der Verbrauchervertrag, 2016), und
   welchen Beruf übt Ullreich aus?" (auto) — 40 s, **correct** (Georg
   Eckert and Alexander Schopper, Institut für Unternehmens- und
   Steuerrecht Innsbruck; Prokuraturanwalt), cited to the book stored at
   22:xx UTC.
8. "Was sagt die ADVOKAT Grundbuch-Beispielsammlung (Stand 18.10.2019) über
   eine offizielle Bedienungsanleitung für Grundbuchanträge?" (auto) —
   55 s, **correct** ("keine offizielle Bedienungsanleitung … von Seiten des
   BRZ, des BMJ oder der Gerichte", examples from real filings), cited to
   both the 2019 and a 2017 edition of the collection (F15: two editions
   side by side); 23 k prompt tokens (F11).

Recurring in the addendum: F5, F7, F11, F15, F18, F22, F23, F38, F43,
F44, F51.
### Tick 35 — 2026-10-29 02:50 UTC (window 23:51 → 02:50, 3.0 h)

Job (5.2 h since the 21:38 resume, 16 night slots): 1,812 files, +258 in
the window of which 242 duplicate skips — **13 real documents** (p50
2,892 s, max 3,550 s, 15,914 chunks: Rechberger AußStrG commentary,
Raschauer Verwaltungsrecht, Schmid, the Konsumentenpolitisches Jahrbuch);
2,753 PDFs left; ETA 38.0 d, `.pdf` window pace 2.0/min (F42). Database
+16 documents / +18,250 chunks (totals 102,496 / 1,447,074); 0 of 16
sampled documents suspect OCR, 128 chunks under 20 tokens (F16), 953 over
512 (F10, F36), 0 duplicate groups by hash. Containers: backend 1,676 % CPU / 78.8 GB (F38), embedder 100 % CPU /
9.9 GB, MongoDB 22 GB. No container restarted since 21:37; the embedder
since 2026-10-26 22:30; chunk budget still 512 (F36).

Embedder (F29, F47): 468 tok/s in the 10-minute sample (1,421 at tick 34),
hourly sequences 8.3 k / 11.1 k / 7.0 k (00:00–02:00 UTC), a 15-token
request 0.7 s at 02:50 (between batches). Backend log (12,383 lines): 96
"Query embedding failed" retries, 34 search request timeouts, 34 HTTP 5xx,
14 `GET /health` timeouts (F7), 11 "Slow request", 4 "no active spec"
(F24). Budget (F43, F44): 36 `TIMEOUT (attempt 1/3)` at 3,600 s, **22 after
a successful conversion — 66,815 s = 18.6 h of conversion discarded in
3 h against 13 documents stored**; 10 conversions killed. With 16 slots
the embedder, not the conversion, decides the pace, and the longer budget
only lets more work be lost at once.

Text quality: Raschauer's "Allgemeines Verwaltungsrecht" (1.57M chars,
1,505 chunks) carries a title page in a shifted font encoding
(";XZQVOMZ[ 3]ZbTMPZJÛKPMZ …") while its body is clean — 2 % vowelless
words overall, so no action beyond the F41 class; none of the 24 documents
stored since the restart is garbage-dominated. Sebastian Schmid's
"Zuständigkeit und Zuständigkeitsübertragung" was stored twice from two
folders (1,404,495 vs 1,404,585 chars, different hashes, F4-type near
copies the hash dedupe cannot see).

Retrieval probes (12): hybrid 2 answered / 10 errors, semantic 3 / 9,
median 30.0 s = the request timeout; the blackout holds while 16 slots
feed the embedder (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 68 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup (Compliance-Management, Kofler-Senoner 2016) — 98 s,
   **correct document named in the answer**, but the sources list shows a
   Schriftsatz and four WEKA Gewerbeordnung registers instead of the book
   (F15: the cited document is not the one listed).
3. Title summary "Die Rechtsanwaltsgesellschaft, Reiner (2016)" — 117 s,
   **honest miss**: "kein Dokument mit diesem exakten Titel", then
   material about a Paul Rainer (fuzzy on the author's name) and about
   Rechtsanwaltsgesellschaften in general; sources the `Beilage B` copies
   (F5, F15). No confabulated summary.

Chat, hand-crafted:

4. "Wer ist der Autor der Monografie 'Zuständigkeit und
   Zuständigkeitsübertragung' (Forschungen aus Staat und Recht, Band 182),
   und wer gibt diese Reihe heraus?" (auto) — 119 s, **correct and
   complete** (assoz. Prof. Dr. Sebastian Schmid, Innsbruck; the series
   edited by Schulev-Steindl with Winkler and Grabenwarter; Verlag
   Österreich 2017), nine of ten sources the two stored copies of the book
   (F4-type near copies, F15).
5. "Wer sind die Herausgeberinnen des Konsumentenpolitischen Jahrbuchs
   2015, und in welcher Schriftenreihe und welchem Band erschien es?"
   (fast) — 89 s (the query embedding waited), **correct** (Maria
   Reiffenstein and Beate Blaschek; Schriftenreihe Verbraucherrecht,
   Verbraucherpolitik, Band 46) — but cited to a 2019 volume of the same
   series that lists the earlier bands, not to the Jahrbuch stored in this
   window, which is absent from the sources (F5, F15).

New: none. Recurring F5, F7, F10, F11, F13, F15, F16, F18, F23, F24, F29,
F36, F38, F41, F42, F43, F44, F47. Not measurable while probes time out:
F25 (control passed), F34, F35, F48.

#### Tick 35 addendum — 02:50–03:20 UTC, second pass on the same window (fork session)

Own measurements: 36 budget timeouts at 3,600 s, 22 of them after a
finished conversion — 65,864 s = 18.3 h of Docling conversion discarded in
3 h, the worst window of the run (F44: the doubled budget lets the
1,300-chunk books convert for 40–55 min and then throws them away while
their chunks queue at the embedder); 10 conversions killed; backend
1,770–1,834 % CPU / 79.5 GB with 16 slots (F38); embedder 484–847 tok/s
hourly, a direct 10-token request 12.8 s at 02:52 and the 300 s chat budget
exceeded twice at 03:0x; 14 `GET /health` timeouts (F7), 34 × 504 and 96
query-embedding retries (30 final failures) on search (F18).

Chat, hand-crafted (fork, 02:55–03:15 UTC, embedder saturated):

6. "Worauf basiert 'Sportgerichtsbarkeit' von Matija Druml (2017), wann und
   an welcher Universität wurde die Arbeit eingereicht, und bis wann wurden
   Literatur und Rechtsprechung berücksichtigt?" (auto) — **HTTP 504 after
   322 s** (F18); the Vorwort (Dissertation, eingereicht im November 2015
   an der Universität Innsbruck, Stand April 2016) is in the book stored at
   00:xx UTC.
7. "Wer ist Georg Jeremias, der Autor von 'Die Deckungsablehnung des
   Versicherers nach § 12 VersVG' (2019), laut der Autorenangabe?" (auto)
   — 154 s, 0 sources, refusal that recommends juris/Beck-Online and the
   book's imprint (F45-type advice; the author note "war
   Universitätsassistent am Institut für Zivilrecht … der
   Karl-Franzens-Universität Graz" is indexed since 01:xx UTC) — the query
   embedding timed out (F18, F29).
8. "Welchen Untertitel trägt Elisabeth Reiners 'Die Rechtsanwaltsgesellschaft',
   und an welcher Einrichtung ist die Autorin tätig?" (auto) — **HTTP 504
   after 300 s** (F18); indexed ("Gesellschaftsrecht, Berufsrecht und
   empirische Befunde"; Forschungsinstitut für Freie Berufe, WU Wien).

Zero of three fork questions answered — the scripted chats minutes earlier
all succeeded, so the blackout is the usual burst pattern (F18): the
window's 16 books hand 1,000–1,350 chunks each to the embedder at once.
Recurring in the addendum: F7, F18, F29, F38, F44, F45.
### Tick 36 — 2026-10-29 05:50 UTC (window 02:50 → 05:50, 3.0 h)

Job (8.2 h since the 21:38 resume, 16 night slots): 2,022 files, +210 in
the window of which 202 duplicate skips — **11 real documents** (p50
2,891 s, max 3,388 s, 11,212 chunks: Buchleitner, Homann, the
Rechtshandbuch Social Media, Kofler-Senoner, Wienbracke); 2,257 PDFs
left; ETA 40.6 d, `.pdf` window pace 0.8/min (F42). Database +16
documents / +16,278 chunks (totals 102,512 / 1,463,352); 1 of 16 sampled
documents suspect OCR, 85 chunks under 20 tokens (F16), 1,349 over 512
(F10, F36), 0 duplicate groups by hash. Containers:
backend 1,325 % CPU / 62.6 GB (F38), embedder 100 % CPU / 9.5 GB, MongoDB
19.6 GB. No container restarted since 21:37; the embedder since
2026-10-26 22:30; chunk budget still 512 (F36). **At 03:29 UTC a
conversion worker was OOM-killed** (F53, new): the backend container's
cgroup reports `oom_kill 1`, the file `Einzelabrechnungen 2023.pdf` was
recorded as `error` ("conversion worker 13195 exited (code None)"), and the
125.6 GiB VM had 33 GB available with 3 GB of its 6 GB swap in use at 05:50.

Embedder (F29, F47): 677 tok/s in the 10-minute sample, hourly sequences
10.2 k / 9.5 k / 9.5 k (03:00–05:00 UTC), a 15-token request over the
60 s client timeout at 05:50. Backend log (12,787 lines): 120 "Query
embedding failed" retries, 41 search request timeouts, 43 HTTP 5xx, 2 HTTP
504 on chat, 3 `GET /health` timeouts (F7), 17 "Slow request", 6 "no
active spec" (F24). Budget (F43, F44): 38 `TIMEOUT (attempt 1/3)` at
3,600 s, 19 after a successful conversion (53,710 s = 14.9 h discarded),
18 conversions killed — the second-worst window after tick 35.

Retrieval probes (12): hybrid 0 answered / 12 errors, semantic 1 / 11,
median 30.0 s = the request timeout — a full blackout again (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 92 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup — not run (no probe answered).
3. Title summary "Buchleitner, Gewährleistung und Irrtum (2018)" — 124 s,
   2 iterations, **correct** (Dr. Christina Buchleitner's comparison of
   Irrtums- and Gewährleistungsrecht), the book among the sources next to
   ABGB-ON commentary excerpts.

Chat, hand-crafted:

4. "Wer sind die Herausgeber des 'Rechtshandbuch Social Media' (2015),
   und welchen Lehrstuhl hat Gerrit Hornung inne?" (auto) — 57 s,
   **correct and complete** (Hornung and Müller-Terpitz; Öffentliches
   Recht, Informationstechnologierecht und Rechtsinformatik, Passau;
   Müller-Terpitz's Mannheim chair as a bonus), from the 1.66M-character
   handbook stored in this window; four of ten sources the `Beilage B`
   copies (F5, F15).
5. "Wer ist die Autorin von 'Gewährleistung und Irrtum – Eine
   Gegenüberstellung' (2018), und wie lautet der Zitiervorschlag?" (fast)
   — 11 s, **correct** (Dr. Christina Buchleitner; "Buchleitner,
   Gewährleistung und Irrtum (2018) [Seite]"), six of ten sources the
   `Beilage B` copies (F15).

New: F53 (a conversion worker OOM-killed with 16 night slots). Recurring
F5, F7, F10, F11, F13, F15, F16, F18, F23, F24, F26, F29, F36, F38, F42,
F43, F44, F47. Not measurable while probes time out: F25 (control passed),
F34, F35, F48.

#### Tick 36 addendum — 05:50–06:20 UTC, second pass on the same window (fork session)

Own measurements: 37 budget timeouts at 3,600 s, 19 after a finished
conversion (53,701 s = 14.9 h discarded, F44), 18 kills and 18 fresh worker
processes; backend 1,390 % CPU / 62.8 GB after the OOM kill (F53: the
cgroup's `oom_kill 1` and `MemAvailable` 33 GB of 131 GB were read at
05:55 UTC too); MongoDB 19.6 GB; embedder 766–893 tok/s hourly, a direct
10-token request 180 s at 05:52 (F47 queue, GPU 0 %); 38 × 504 and 114
query-embedding retries (32 final) on search, 3 `GET /health` timeouts
(F7). The Ziehensack loose-leaf (`Ziehensack, Schriftsätze für
Rechtsanwälte - Band 1`, a 2011 RDB export) is stored with Mac Roman
mojibake throughout ("SchriftsŠtze fŸr RechtsanwŠlte", F39).

Chat, hand-crafted (fork, 06:00–06:05 UTC, between two embedding bursts):

6. "An welcher Universität wurde 'Der Künstlermanagementvertrag' von
   Hans-Jürgen Homann als Dissertation angenommen, wann fand das Rigorosum
   statt und wer war der Erstgutachter?" (auto) — 74 s, **correct and
   complete** (Freie Universität Berlin, 11. Juli 2012, Univ.-Prof. Dr.
   Martin Schwab; Zweitgutachter Jochem Schmitt), five of six sources the
   book itself.
7. "Worum geht es laut dem Vorwort in Christina Buchleitners 'Gewährleistung
   und Irrtum – Eine Gegenüberstellung' (Manz 2018)?" (auto) — 76 s,
   **correct in substance** (the overlap of Gewährleistung and Irrtum, the
   "rediscovery" of the Irrtumsrecht in Anlegerprozesse and
   Liegenschaftskäufe) but answered from the Einleitung rather than the
   Vorwort's own sentence about the Konkurrenz; 30 k prompt tokens (F11).
8. "Was bringt laut dem Vorwort die 6. Lieferung von Ziehensacks
   'Schriftsätze für Rechtsanwälte, Band 1' an Aktualisierungen?" (auto) —
   96 s, **correct** (Berufung und Berufungsbeantwortung, Revision,
   Vertagungsbitte, Streitverkündigung), all six sources the loose-leaf,
   read correctly despite the mojibake in the stored text (F39 is cosmetic
   for retrieval here, the embedder tolerates it).

Three of three fork questions correct. Recurring in the addendum: F7, F11,
F39, F44, F47, F53.
### Tick 37 — 2026-10-29 08:50 UTC (window 05:50 → 08:50, 3.0 h)

Slots: 16 until 07:00:09 UTC, then `8 of 16 (office hours, load profile
office)`; a performance-config save at 07:50:10 (concurrent_files 16,
office) changed nothing (F49 applied live). No container restarted since
21:37; the embedder since 2026-10-26 22:30; no further OOM kill (F53).

Job (11.2 h since the 21:38 resume): 2,144 files, +122 in the window of
which 42 duplicate skips — **75 real documents** (p50 162 s, p99 2,917 s,
7,683 chunks): the queue has left the 1M-character books for case files
again (Schriftsätze, Direktzustellungen, GeKo commentary excerpts); 1,940
PDFs left; ETA 41.8 d, `.pdf` window pace 1.1/min (F42). Database +78
documents / +11,996 chunks (totals 102,590 / 1,475,348); 1 of 77 sampled
documents suspect OCR, 98 chunks under 20 tokens (F16), 435 over 512 (F10,
F36), 0 duplicate groups by hash. Containers:
backend 403 % CPU / 52.6 GB (8 slots), embedder 100 % CPU / 9.5 GB,
MongoDB 21 GB; VM MemAvailable 45 GB, swap 3.3 of 6 GB free.

Embedder (F29, F47): 558 tok/s in the 10-minute sample, hourly sequences
10.1 k / 7.5 k / 5.7 k (06:00–08:00 UTC), a 15-token request over the 60 s
client timeout at 08:50. Backend log (27,521 lines): 137 "Query embedding
failed" retries, 49 search request timeouts, 54 HTTP 5xx, 6 `GET /health`
timeouts (F7), 13 "Slow request", 5 "no active spec" (F24). Budget (F43,
F44): 26 `TIMEOUT (attempt 1/3)` at 3,600 s, 18 after a successful
conversion (46,179 s = 12.8 h discarded), 6 conversions killed.

Fixes in flight: PR #31 (F35 picture-page OCR fallback), PR #32 (F34
statute citations as identifiers) and PR #33 (F44 budget stops at the
conversion, late embeddings stored partially) were opened at 19:23–19:30
UTC on 10-28 by the three fix sessions; none deployed.

Retrieval probes (12): 0 of 24, every request at the 30 s timeout (F13,
F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 276 s, 0 sources, "keine Unterlagen … vor": the
   retrieval outage as a refusal (F18); the first control failure since
   tick 30.
2. Sentence lookup — not run (no probe answered).
3. Title summary "20191219 Kaufvertrag unterfertigt" — 165 s, **honest
   miss** ("kein spezifisches Dokument mit dem exakten Titel"), then a
   summary of other Kaufanbote and an insurance statement (F5).

Chat, hand-crafted:

4. "Wer kommentiert § 30 MRG (Kündigungsbeschränkungen) im GeKo Wohnrecht
   I, und was bestimmt § 30 Abs 1 MRG laut Normtext?" (auto) — 98 s,
   **correct** (Lovrek; Kündigung nur aus wichtigen Gründen, with the Z 1–3
   examples from the Normtext), from the commentary excerpt stored at
   07:xx UTC — but the Abs 1 sentence is cited to an LVwG Wien decision
   instead of the commentary (F15 citation drift); seven of ten sources
   other GeKo excerpts and OGH decisions.
5. "Welche Umzugsfirma erstellte den Kostenvoranschlag Nr. 1201706049 für
   die AKG Acoustics GmbH, und wer war der Ansprechpartner bei AKG?" (fast)
   — 28 s, **honest miss**: the Direktzustellung that contains the
   Kostenvoranschlag (`P-56-17/webErv/Direktzustellung_18929_90503_2018_03_05.pdf`,
   Hauer-Kopal Übersiedlungen, Richard Soyka) fills three of ten source
   slots, but the chunks passed were other pages of it and AKG termination
   letters, so the answer says the number does not occur (F22: the header
   chunk with the firm and contact was not among the passed ones). No
   confabulation.

New: none. Recurring F5, F7, F10, F11, F13, F15, F16, F18, F22, F23, F24,
F29, F36, F42, F43, F44, F47, F49. Not measurable: F25 (control failed on
the outage), F34, F35, F48.

#### Tick 37 addendum — 08:50–09:25 UTC, second pass on the same window (fork session)

Own measurements: office profile since 07:00 UTC (`File slots: 8 of 16`),
two performance-config saves at 07:50 and 07:51 that kept 8 slots; backend
417–502 % CPU / 51 GB, no further OOM kill (`oom_kill 1` unchanged,
`MemAvailable` 47 GB, F53); 26 budget timeouts at 3,600 s, 18 after a
finished conversion (46,170 s = 12.8 h discarded, F44), 6 kills; embedder
437–784 tok/s hourly, a direct 10-token request 154 s at 08:52, 47 × 504
and 134 query-embedding retries (42 final) on search, 6 `GET /health`
timeouts (F7). The window's 78 documents are mostly case files again (p50
164 s) after the book stretch.

Chat, hand-crafted (fork, 09:05–09:20 UTC, embedder saturated):

6. "Wer verkauft laut dem Kaufvertrag vom 07.08.2014 die Liegenschaft EZ 497
   KG 06315 Zwerndorf an wen, zu welchem Kaufpreis, und wie hoch sind
   Grunderwerbsteuer und Eintragungsgebühr?" (auto) — **HTTP 504 after
   300 s** (F18) although the identifier path logged `Identifier query
   ['EZ 497 KG 06315'] … 1 exact hit` at 09:13:28 — retrieval found the
   deed (`LambCh_LambNi-Verl/134-14 Kaufvertrag, 07 08 2014.pdf`, 08:26
   UTC: Ing. Erich Lambert to Richard and Elfriede Lawendar, EUR 45.000,
   GrESt 1.575, Eintragungsgebühr 495), the remaining stages did not
   finish inside the 300 s budget.
7. "Wer klagt wen im Verfahren 28 C 1043/18p vor dem Bezirksgericht
   Floridsdorf laut der Äußerung vom 04.12.2018, wie hoch ist der
   Streitwert und wer vertritt den Beklagten?" (auto) — 285 s, 0 sources,
   no-result fallback with the English query plan (F45); the ERV form
   prints "028 C 1043/2018p" (F48 spelling) and the query embedding timed
   out (F18), so whether the identifier path would have matched the
   lawyer's spelling is not decided by this run. Indexed: Lizbeta
   Ballabani v. Mag. Walter Pirker, EUR 9.340,14, Dr. Stephan Trautmann
   for the defendant (`MagdPe_BallLi/Gericht/Äusserung.PDF`, 08:17 UTC).

Zero of two fork questions answered. Recurring in the addendum: F7, F18,
F44, F45, F48 (not testable), F53.

### Tick 38 — 2026-10-29 11:50 UTC (window 08:51 → 11:50, 3.0 h)

Written by the fork session at 20:30 UTC: the loop session was stalled
from 12:30 to 20:27 UTC (the 14:50 and 17:50 firings were lost), so this
section is late and tick 39 covers 11:51 → 20:27.

Job (14.2 h since the 21:37 recreate): 2,301 of 296,435 files, +157 in the
window of which 3 duplicate skips — **150 real documents** (p50 193 s, p90
1,047 s, 16,787 chunks; 93 of them pure scans OCRed successfully, F35
holds for text-classified scans), 4 new `image_only_pdf`; 1,536 PDFs left;
ETA 43.5 d, `.pdf` window pace 0.8/min (F42). Office profile, 8 slots.
Database totals 102,740 documents / 1,492,135 chunks; 9 of 146 sampled
documents suspect OCR (F12), 158 chunks under 20 tokens (F16), 668 over
512 (F10, F36). Containers: backend 808 % CPU / 56.6 GB (F38), MongoDB
20–27 % / 20 GB, embedder 0.1–75 % CPU (bursty), `oom_kill` still 1 (F53).

Budget (F43, F44): 8 `TIMEOUT (attempt 1/3)` at 3,600 s, 3 after a
finished conversion (6,103 s discarded), 2 kills — the case-file stretch
is cheap. Embedder (F29, F47): 387–563 tok/s hourly, 121 tok/s in the
10-minute sample, GPU 0 %, a direct 10-token request 0.9 s at 11:52 (quiet
moment) — but the backend logged 204 query-embedding retries (65 final
failures), 48 × 504 and 6 × 500 on search in the window (F18).

Retrieval probes (12): hybrid 6 answered / 6 errors, hit@5 5, median
2.3 s; semantic 8 / 4, hit@5 3, median 0.36 s (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — **HTTP 504 after 300 s** (F18).
2. Sentence lookup (Straferkenntnis, Anzeige vom 03.07.2017) — 284 s,
   2 iterations, 0 sources, "keinen spezifischen Text … gefunden": the
   retrieval timed out behind the embedder (F18), honest.
3. Title summary "w6 SR_D&F_20200405_freigabe" — 146 s, 0 sources,
   no-result fallback with the English query plan (F45).

Chat, hand-crafted (fork, 12:05–12:15 UTC):

4. "Wer erstattete im Verfahren 58 Cg 50/16y (Doris Grössinger gegen Dr.
   Georg Grabmeier) das orthopädische Gutachten vom 03.08.2017, und welche
   Operation wurde laut der Vorgeschichte am 27.03.2012 geplant?" (auto) —
   285 s, 0 sources, no-result fallback with the English query plan (F45;
   the Gutachten of Priv. Doz. Dr. Roman Radl, PLIF L4/5, stored 09:19 UTC,
   is indexed); query embedding timed out (F18).
5. "Worüber soll laut dem Gerichtsauftrag (Beschluss vom 21.02.2019) das
   unfallchirurgische Gutachten im Verfahren Gerhard Wechdorn gegen die
   Generali Versicherungs-AG Auskunft geben, und wer ist die
   Sachverständige?" (auto) — 271 s, **wrong with full confidence**: the
   answer names "Dr. Elisabeth Posch" as the appointed expert and reports a
   EUR 1.800 Kostenvorschuss, both cited to `TautTh_VU/Gericht/Sonstige
   Erledigung, Protokoll-1` — a hearing protocol of another matter; the
   asked Gutachten (`Generali_WechGe/Unterlagen/Generali-Wechdorn
   Gutachten.pdf`, 09:19 UTC: Dr. Tina-Maria Donner-Wielke; Art der
   Verletzung, Dauer und Intensität der Schmerzen, Spät- und Dauerfolgen
   after the 07.05.2018 motorcycle accident, EUR 8.032,45 s.A.) was not
   among the ten sources, only a Honorarnote of the matter was (F20, F5).
   Donner-Wielke's name appears only in a trailing remark.

New: none. Recurring F5, F10, F12, F13, F16, F18, F20, F29, F35, F36,
F38, F42, F43, F44, F45, F47, F53. Not measurable: F25 (control timed out).
#### Tick 38 addendum — 11:50–12:05 UTC, second pass on the same window (original session, written 20:35 UTC)

Own scripted run at 11:50: probes 0 of 24 (the fork's run minutes later
got 6 and 8 through — the queue flickers), control 298 s refusal with 0
sources (F18), title summary "Aktabschrift Bognar Teil 1" 279 s no-result
fallback with the English query plan (F45); 2 `GET /health` timeouts (F7),
5 "no active spec" (F24).

Chat, hand-crafted (original session, 11:55–12:00 UTC):

6. "Worum geht es in der Monografie 'Gesetzeskonforme Methodik' von
   Johannes Kehrer (2013), und welche ABGB-Bestimmungen stehen im
   Mittelpunkt?" (auto) — 175 s, **correct and rich** (§§ 6 and 7 ABGB,
   the Einheitskonzept, the Drei-Stufen-Modell, the verfassungskonforme
   Auslegung argument), cited to the monograph stored at 10:xx UTC; four of
   ten sources the `Beilage B` copies (F15), 12.6 k prompt tokens (F11).
7. "Für welche Rechtssache und welches Gericht wurde das chirurgische
   Sachverständigengutachten in der Sache Ballabani gegen Dr. Magdics
   erstellt, und wer ist der zuständige Richter?" (fast) — 35 s,
   **correct** (28 C 336/15, Bezirksgericht Floridsdorf Abteilung 28, Rat
   Mag. Werner Humel); eight of ten sources unrelated (F5).

The loop session itself was stalled from 12:30 to 20:27 UTC, so the 14:50
and 17:50 firings were lost; tick 39 covers 11:51 → 20:27.

New (addendum): none. Recurring in the addendum: F5, F7, F11, F15, F18,
F24, F45.
### Tick 39 — 2026-10-29 20:27 UTC (window 11:51 → 20:27, 8.6 h)

The loop session was stalled from 12:30 to 20:27 UTC (the 14:50 and 17:50
firings were lost; the fork session wrote tick 38 at 20:30), so this tick
covers 8.6 h.

**The PDF stretch is over**: the queue moved into the law firm's Word
archives at 17:xx UTC. Job (22.9 h since the 21:38 resume): 12,851 of
296,435 files, +10,550 in the window — **9,380 real documents** (p50 15 s,
p99 204 s, 50,671 chunks: 7,151 `.doc`, 1,743 `.rtf`, 258 `.pdf`, 255
`.docx`, 15 `.msg`), 1,049 `error` records (F54, new), 152 duplicate skips,
845 PDFs left. Slots 16 (night profile) since 17:00 UTC. **ETA 291 d**
(43.5 d at tick 38): the `.msg` window pace of 0.57/min from 15 successes
prices 85,748 files at 104 d and `.wbk` gets a lifetime pace of 0.04/min
(F42). Database +9,386 documents / +53,996 chunks (totals 112,027 /
1,545,918); 3 of 300 sampled documents suspect OCR, 1,060 chunks under 20
tokens (F16), 1,628 over 512 with a new maximum of 735 — the simple
fallback ignores the chunk budget (F10, F36, F55); 0 duplicate groups by
hash. Containers at 20:27: backend 1.5 % CPU / 47 GB (the 16 slots
wait for the embedder), embedder 100 % CPU / 9.6 GB, MongoDB 21 GB; no
further OOM kill (F53), VM MemAvailable 50 GB; chunk budget still 512
(F36); no container restarted since 21:37 on 10-28.

Chunking (F55, new): 7,841 "No DoclingDocument provided, using simple
chunking fallback" warnings — 25,569 of the window's 54,083 chunks are
`simple_fallback` (avg 226 tokens, 779 under 20) because antiword and
striprtf return plain text.

Embedder (F29, F47): 523 tok/s in the 10-minute sample, hourly sequences
5.3 k → 9.0 k (14:00–19:00 UTC), a 15-token request 14 s at 20:27; the
small Office files are embedder-bound (backend CPU 1.5 %). Backend log
(165,479 lines): 3 "Query embedding failed" retries, 2 search request
timeouts, 2 HTTP 5xx (the loop was not probing for 8 h), 10 "Slow
request", 966 tracebacks (F54). Budget (F43, F44): 3 `TIMEOUT (attempt
1/3)`, all after a finished conversion (6,302 s discarded), 0 kills.

Retrieval probes (12): hybrid 12 answered, hit@1 7, hit@5 11, hit@10 12,
median 17.3 s; semantic 11 / 1, hit@5 5, median 15.7 s — every request got
through, each a few seconds under the 30 s budget (F13, F18); the short
Office letters are easier probes than OCR'd scans.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 96 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup (Eigenhändiges Testament template) — 88 s, **correct**
   document and type, but the sources list shows five unrelated filings
   (F15).
3. Title summary — not run by the script this tick.

Chat, hand-crafted:

4. "Was rät Dr. Friedrich Flendrovsky Kurt Rovina im Schreiben vom
   06.07.1998 zur Pflichtteilsergänzungsklage seines Bruders, und welche
   Kosten erwähnt er?" (auto) — 84 s, **honest miss**: the letter
   (`Pittner Archiv/TAG98-2.HJ/6-7ROV.DOC`, stored 18:26 UTC: the claim is
   misdirected, the Pflichtteilsergänzungsanspruch goes against the
   Nachlass first, S 25.000 of Schätzkosten expected) was not among the ten
   sources — the archive holds dozens of Rovina/Flendrovsky letters and the
   DOS-era files date themselves "Wien,am 06.07.98", so the question's
   "06.07.1998" matched a 1997 cost letter instead; the answer says no such
   letter exists and summarises the 1997 one (F5, F17-type date spelling).
   No confabulation.
5. "Was teilte der Ausschuss der Rechtsanwaltskammer Wien Dr. Axel
   Friedberg am 7.1.1997 zur mittlerweiligen Stellvertretung Dr.
   Bilowitzki mit?" (fast) — 27 s, **correct** (§ 34 RAO, not authorised to
   sign the requested declaration, Mag. Kienast to settle the
   Verwendungszeugnis with Dr. Bilowitzki), from `Czinglar
   Archiv/server/RAK/4982_311.DOC` stored at 20:13 UTC; eight of ten
   sources other archive letters (F5).

New: F54 (Word lock files queued and failing with tracebacks), F55
(legacy .doc/.rtf text cut by the simple chunking fallback, up to 735
tokens). Recurring F5, F10, F11, F13, F15, F16, F18, F23, F29, F36, F42,
F44, F47. Not measurable: F7 (no health timeouts while the loop was
stalled), F34, F35, F48.

#### Tick 39 addendum — 20:30–20:50 UTC, second pass on the same window (fork session)

Own measurements for the 8.6 h window (11:51 → 20:27 UTC): 3 budget
timeouts (all after a finished conversion, 6,300 s discarded, F44), 0
kills, 0 `GET /health` timeouts, 4 search requests all 200, 0
query-embedding retries, `oom_kill` still 1 (F53), backend 47 GB and
0.5–0.7 % CPU at 20:28 (the slots wait on the embedder: one "Generating
embeddings for 1 chunks" round trip per tiny `.doc`, F29/F37-type
batching), embedder 450–615 tok/s hourly, GPU 12 %. Per-file stats: 9,241
real documents (7,139 `.doc`, 1,709 `.rtf`, 264 `.pdf`, 106 `.docx`, 15
`.msg`, 7 `.eml`; p50 15 s, p90 45 s), 1,049 `error` (F54: 575 lock
stubs of 162 bytes + 474 antiword rejections; 9 `.msg`/`.dot` "does not
contain a property stream", F21), 152 duplicates, 167 scans OCRed. The
per-extension ETA now extrapolates 85,748 `.msg` at the window pace of
0.68/min (15 files) to 2,100 h and reads 282.6 d (F42, the other way
round this time).

Chat, hand-crafted (fork, 20:31–20:45 UTC, against the Pittner archive
`.DOC` letters of 1996–98 stored in this window):

4. "Was übermittelte Dr. Thomas Pittner am 19. September 1996 per Telefax
   an Dr. Thomas Francan in der Sache Siegfried und Ingeborg Pust, und
   worum ersuchte er?" (auto) — 77 s, **correct** (drei Aktenvermerke und
   ein Briefentwurf; Prüfung und Freigabe des Entwurfs vor dem Fax an Herrn
   Pust), all six sources the matter's letters of the same days.
5. "Was empfahl Dr. Friedrich Flendrovsky der Munters Trocknungs-Service
   GmbH in der Sache Format Bau GesmbH nach dem Konkursbeschluss vom
   09.06.1998, und welche Kosten waren angefallen?" (auto) — 137 s,
   **correct** (Forderung als uneinbringlich abschreiben; Konkurs mangels
   Kostendeckung aufgehoben; the Krida remark), the cost lines (ATS 120 +
   170 = 290) cut off by the answer length; 30 k prompt tokens (F11); six
   sources all Munters letters of 1997–98.
6. "Wer ist Antragsteller im Grundbuchsantrag auf Vorrangeinräumung vom
   02.03.2021 an das Bezirksgericht Mistelbach (Akt P-63B/13), wer vertritt
   ihn und wer ist Beteiligte?" (auto) — 258 s, **correct** (KARL Holding
   GmbH, FN 67494g; Dr. Thomas Pittner; Volksbank Wien AG, Dorothee Korab,
   Margaretha Karl), from the `.rtf` ERV form (simple-fallback chunks,
   F55, were good enough here); four of six sources unrelated (F5).

Three of three fork questions correct — the plain-text readers' output
retrieves well for short letters even without the hybrid chunker (F55 is
about structure and context, not basic recall). Recurring in the
addendum: F5, F11, F21, F29, F42, F44, F53, F54, F55.
### Tick 40 — 2026-10-29 20:50 UTC (window 20:29 → 20:50, 0.35 h)

A 21-minute window: the 20:23 firing arrived right after the delayed
tick 39.

Job (23.2 h since the 21:38 resume, 16 night slots): 13,831 files, +980
in 21 min — **1,055 real documents** (p50 19 s, 2,760 chunks: 521 `.docx`,
397 `.doc`, 124 `.rtf`, 9 `.msg`, 3 `.dot`), 1 error, 0 timeouts; ETA
268 d (`.doc` 25/min, `.docx` 9.7/min, `.msg` still 0.55/min from a dozen
files, F42). Database +1,088 documents / +2,953 chunks (totals 113,045 /
1,548,553); 7 of 300 sampled documents suspect OCR (F12), 39 chunks under
20 tokens (F16), 39 over 512 with a maximum of 668 (F10, F36, F55).
Containers: backend 3.5 % CPU / 47 GB (the 16 slots wait for
the embedder), embedder 100 % CPU / 9.6 GB, MongoDB 22 GB; `oom_kill`
still 1 (F53), VM MemAvailable 50 GB; chunk budget 512 (F36); no
container restarted since 21:37 on 10-28, the embedder since 10-26 22:30.

Embedder (F29, F47): 530 tok/s in the 10-minute sample, a 15-token request
10.4 s at 20:50 — the Office stretch is embedder-bound: 540 "simple
chunking fallback" warnings in 21 min (F55), 14 "Query embedding failed"
retries, 3 search request timeouts, 6 HTTP 5xx, 56 "Slow request", 2
"requires antiword" skips (F54).

Retrieval probes (12): hybrid 8 answered / 4 errors, hit@5 7, median
22.4 s; semantic 11 / 1, hit@5 4, median 20.1 s — most requests squeeze
under the 30 s budget, 20 s of each spent waiting for the query embedding
(F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 81 s, **correct**, ten Firmenbuchabfrage sources.
2. Sentence lookup (Aktenvermerk "30-10-12 Kom Gmoser", a `.docx` of this
   window) — 111 s, **correct** document and content (Schenkungsvertrag,
   GrESt-Erlagschein); four of five listed sources unrelated (F15).
3. Title summary "Muster 11.2202 Löschung und Eintragung von
   Geschäftsführern" — 158 s, **correct** (a Firmenbuch template), the
   template among the sources in two copies.

Chat, hand-crafted:

4. "Welchen Mietzinsrückstand fordert Dr. Trautmann im Schreiben vom
   16.02.1998 an Dr. Gerald Preimesberger (Fulda-Pavlides / Preimesberger),
   und wie werden die Reparaturkosten der Therme verrechnet?" (auto) —
   232 s, 2 iterations, **wrong amount**: the answer names S 5.115,-- as
   the Mietzinsrückstand — that is the Therme repair total the firm offsets
   against the October rent; the letter demands S 36.610,-- (S 3.230 rest
   of October plus four months at S 8.345), which sits in the letter's
   third fallback chunk that was not passed (the 2,000-character letter is
   cut into three overlapping chunks of ~320 tokens, F55); the offset
   mechanism and the USt condition are right, extra context from the
   same-day Aktenvermerk `16-2PAV` is plausible (F20, F22).
5. "Welchen Betrag bietet Dr. Thomas Pittner im Schreiben vom 28.02.1996
   an Dr. Georg Röhsner für den Mietrechtsverzicht von Gertrud Wagner an?"
   (fast) — 24 s, **wrong**: "kein konkreter Geldbetrag" although the
   letter offers S 70.000,-- bei gegenseitiger Kostenaufhebung; the
   1,531-character letter is three overlapping fallback chunks and the one
   holding the amount was not among the passed chunks, so the absence was
   asserted from the others (F22, F55); the conditions (bestandfrei,
   Mietrechtsverzicht) are right.

New: none. Recurring F10, F11, F12, F13, F15, F16, F18, F20, F22, F29,
F36, F42, F47, F54, F55 — the two wrong answers are the first measured
cost of F55: short letters split mid-sentence into three chunks lose the
one with the number.

#### Tick 40 addendum — 20:50–21:20 UTC, second pass on the same window (fork session)

Own measurements for the 21-minute window: 1,121 real documents (564
`.docx`, 410 `.doc`, 134 `.rtf`, 9 `.msg`; p50 17 s, p90 29 s) — 53
documents per minute, 464 "Processing file" lines in 10 minutes; 0
timeouts, 0 kills, 0 `GET /health` timeouts; backend 2–5 % CPU / 47 GB
(the slots wait on the embedder, one small request per file), embedder
535 tok/s, GPU 4 %; 77 search requests 200, 4 × 500, 3 × 504, 14
query-embedding retries (F18 milder with the small files). The ETA
(F42) reads 268–271 d because 85,730 `.msg` are extrapolated at the
window pace of 0.54/min from nine files.

Chat, hand-crafted (fork, 20:52–20:58 UTC):

4. "Worauf wurde laut dem Aktenvermerk vom 23.4.2014 in der Sache Adlitzer
   (Ehescheidung / Klage, BG Baden, Akt Cz-1444A) die nächste Verhandlung
   erstreckt, und was war mit der Richterin vereinbart?" (auto) — 181 s,
   **correct and complete** (4.7.2014, 11.30 Uhr, Saal I; the judge will
   stretch the case until the divorce and the Aufteilungsverfahren are
   done; the Veräußerungs- und Belastungsverbot reason), from the `.docx`
   stored at 20:32 UTC, five of six sources from the CZ-1444 matters.
5. "Was hielt der Aktenvermerk vom 26.3.1997 für Dr. Berger in der
   Strafsache gegen Dr. Wessner zu Punkt II der Anklage fest, und wann
   wurde der Insolvenzantrag beim Handelsgericht überreicht?" (auto) —
   136 s, **half answered**: Punkt II ("völlig verfehlt", the SVGA page 21
   quote, the Jahreswende 1993/94 advice) correct, but "Informationen zum
   genauen Datum … liegen nicht vor" although the same memo states "Am 4.
   Mai 1994 wurde der Insolvenzantrag (PD: Ausgleich!) beim Handelsgericht
   überreicht" — the second half of the 4.5k-character memo is a
   simple-fallback chunk (F55) that was not among the passed ones, and the
   absence was asserted (F22); four of six sources an unrelated
   "VER-CTA" template in four copies (F15, F5); 27 k prompt tokens (F11).

Recurring in the addendum: F5, F11, F15, F18, F22, F42, F55.
### Tick 41 — 2026-10-29 23:50 UTC (window 20:50 → 23:50, 3.0 h)

Job (26.2 h since the 21:38 resume, 16 night slots): 23,036 of 296,435
files, +9,205 in the window — **9,190 real documents** (p50 19 s, 25,094
chunks: 5,838 `.docx`, 2,380 `.doc`, 442 `.dot`, 274 `.rtf`, 179 `.msg`,
47 `.wbk`), 40 errors (none a lock file this time, F54), 10 duplicate
skips, 9 no-chunk files, 0 timeouts; ETA 107.6 d (268 d at tick 40, F42).
Database +9,136 documents / +24,989 chunks in the script's window (totals
122,184 / 1,573,549); 6 of 300 sampled documents suspect OCR (F12), 266
chunks under 20 tokens (F16), 463 over 512 with a maximum of 939 (F10,
F36, F55); 0 duplicate groups by hash. Containers: backend 0.4 % CPU / 46 GB (the slots wait on the
embedder), embedder 100 % CPU / 9.6 GB, MongoDB 22 GB; `oom_kill` still 1
(F53), VM MemAvailable 51 GB; chunk budget 512 (F36); no container
restarted since 21:37 on 10-28, the embedder since 10-26 22:30.

Embedder (F29, F47): 613 tok/s in the 10-minute sample, hourly sequences
8.2 k / 8.4 k / 7.2 k (21:00–23:00 UTC), a 15-token request 20.9 s at
23:50 — 51 documents per minute are embedder-bound at ~3 chunks each.
Backend log (137,647 lines): 1 "Query embedding failed" retry, 1 search
request timeout, 1 HTTP 5xx, 2 "Slow request" (the loop was quiet), 76
"requires antiword" skips (F54), 2,630 "simple chunking fallback" warnings
(F55), 38 tracebacks.

Retrieval probes (12): hybrid 10 answered / 2 errors, hit@1 6, hit@5 10,
hit@10 10, median 11.1 s; semantic 12 / 0, hit@1 5, hit@5 8, median
10.5 s — the best probe round of the Office stretch, every request well
under the 30 s budget, roughly half of each spent on the query embedding
(F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 52 s, **correct**, Firmenbuchabfrage sources.
2. Sentence lookup (Aktenvermerk "21-01-08 AV", a `.doc` of `CZ-1029`
   stored in this window) — 99 s, **correct** document and content (Dr.
   Czinglar's call with Mag. Grobauer on 21.1.2008); four of the five
   listed sources unrelated (F15).
3. Title summary "Leistungsaufstellung" — 86 s, **honest**: the title names
   dozens of documents, the answer says so and summarises the Honorarnote
   178/21 copies it was given (three copies of the same PDF among the
   sources, F11); the sampled document itself not among the sources — a
   weak probe (generic title), not a retrieval fault.

Chat, hand-crafted:

4. "Wer sind Vermieterin und Mieterin im Mietvertrag ab 1.11.2023 über die
   Wohnung W 10 in der Stifterstraße 30, Ottensheim, welche Räume umfasst
   der Mietgegenstand, und wie lange läuft der Vertrag?" (auto) — 65 s,
   1 iteration, **correct and complete**: Renate Sigl / Beatriz Pomar, W 10
   im 2. Stock, Küche, Bad, WC, 2 Zimmer, Wohnzimmer, Vorraum, Abstellraum,
   Loggia plus Garage 023, 67,89 m², 1.11.2023 bis 31.10.2026 (three years,
   no notice needed); the `.docx` (`SIGL-MV/Attachements/Mietvertrag
   01.11.2023Beatriz Pomar.docx`, stored in this window) is the first
   source, the other nine are unrelated Mietvertrag copies and templates
   (F15).
5. "Worum ging es laut Aktenvermerk vom 8.3.2017 in der Sache Leskovar –
   Verlassenschaft, und wie würde der Mandant im Todesfall seine
   Eigentumswohnung und seine Kinder bedenken?" (fast) — 26 s, **half
   right**: the 40-minute meeting, the rented Eigentumswohnung in his name
   and the statutory quotas (Gattin 4/12, each child 2/12) are correct and
   all from the first of the document's two hybrid chunks; the actual plan
   in the second chunk — Eigentumswohnung to the Gattin, Gattin as
   Alleinerbin, the four children on the Pflichtteil, the mirrored will for
   the 40 % Bauernhof share — is missing, so the "how would he provide"
   half is answered with the status quo instead of the intended testament
   (F22: fast mode answers from the chunk it got and does not fetch the
   neighbouring chunk of the same 2.5 KB document). The `.docx`
   (`Tag17-1.HJ/08-03-17 AV Leskovar.docx`, stored 21:19 UTC) is the first
   source; the other nine are unrelated Verlassenschaft filings (F15).

New: none. Recurring F10, F11, F12, F13, F15, F16, F18, F22, F29, F36,
F42, F47, F54, F55 — a clean tick: the `.docx` stretch goes through the
HybridChunker and both hand-crafted questions found their document first;
the only loss is fast mode stopping at one chunk of a two-chunk memo.
Not measurable: F7, F34, F35, F44, F48 (no timeouts, no scans, no
statute questions in the window).

#### Tick 41 addendum — 23:50–00:20 UTC (2026-10-30), second pass on the same window (fork session)

Own measurements: 9,162 real documents in 3 h (5,809 `.docx`, 2,380
`.doc`, 442 `.dot`, 275 `.rtf`, 179 `.msg`, 47 `.wbk`; p50 18 s, p90
31 s), 40 `error` records, all antiword rejections of small legacy
`.doc`/`.dot`/`.wbk` files (F54, no lock stubs left in this stretch), 9
`no_chunks`; 0 timeouts, 0 kills, 0 `GET /health` timeouts, `oom_kill`
still 1 (F53). Backend 0.4–6 % CPU / 46 GB — the 16 slots wait on the
embedder (one request per small file), embedder 490–675 tok/s hourly, GPU
0 %, a direct 10-token request 16.9 s. The newest chunks reach 549–939
tokens (simple-fallback chunks of `.docx`/`.rtf` text, F55/F10). ETA
105.8 d: 85,368 `.msg` at 0.65/min (F42).

Chat, hand-crafted (fork, 23:52–00:00 UTC):

4. "Was sieht der Vergleichsentwurf im Verfahren 43 Cg 87/23a (BeauMed
   Concepts gegen Linda Bogácsi) vor?" (auto) — 71 s, **correct** (EUR
   13.000 pauschal; 6.000 binnen 14 Tagen, 7.000 in sieben Monatsraten zu
   1.000 ab 04.11.2024; Terminsverlust nach 14-tägiger Nachfrist), cited
   to the `.docx` draft stored at 22:21 UTC; the sources list shows the
   matter's court deliveries instead of the draft (F15-type listing).
5. "Wer sind laut dem Grundbuchauszug in der Beschlussausfertigung vom
   14.11.2012 die Eigentümer der EZ 1886 KG 30023 Steinbrunn, und welches
   Pfandrecht ist in C-LNR 1 eingetragen?" (auto) — 83 s, **half**: the
   pledge is right (UniCredit Bank Austria AG, Höchstbetrag EUR 371.400,
   Pfandurkunde 2011-06-10), but the owners given are the buyers Mag.
   Klaus Limpl and Karin Steiger from the later Grundbuch_Beschluss of
   2012-12-13, while the quoted extract (Grundbuchstand before the
   transfer) lists Ing. Alexander Schmidt and Karin Sofka — the answer
   switched to the sibling decision's state without saying so (F20).
6. "Warum wurde laut dem Protokoll der außerordentlichen Generalversammlung
   vom 6.6.2019 die Statutenänderung des Vereins 'Kinder- und
   Familienprojekt Brigittaplatz' vorgenommen, und wie fiel die Abstimmung
   aus?" (auto) — 59 s, **correct** (Steuerbegünstigung für Spenden,
   Punkte II. und III., einstimmig), with the agenda `TO_06_06_19` and
   the Papierkorb copy of the protocol among the sources (F4/F15).

Two and a half of three fork questions correct. Recurring in the
addendum: F4, F10, F15, F20, F42, F53, F54, F55.
### Tick 42 — 2026-10-30 02:50 UTC (window 23:50 → 02:50, 3.0 h)

Job (29.2 h since the 21:38 resume, 16 night slots): 35,865 of 296,435
files, +12,829 in the window — **12,912 real documents** (p50 12.5 s, p99
43 s, 25,079 chunks: 9,368 `.doc`, 1,581 `.wbk`, 1,405 `.docx`, 426 `.dot`,
87 `.msg`, 37 `.rtf`), 8 errors (5 unreadable `.docx`, F57 new; 3
`requires antiword`, F54), 4 duplicate skips, 1 no-chunk file, 0 timeouts;
the Word archives run at 71 files/min, the fastest stretch of the job.
Database +12,810 documents / +24,988 chunks in the script's window
(totals 134,997 / 1,598,543); 0 of 300 sampled documents suspect OCR, 694
chunks under 20 tokens (F16), 244 over 512 with a maximum of 711 (F10,
F36, F55); 0 duplicate groups by hash. ETA 127.6 d (107.6 d at tick 41,
F42): the job's lifetime rate is 20.7 files/min against 71/min in this
window, and `.msg` is still priced from 697 files. Containers: backend 2.4 % CPU / 46 GB, embedder 100 % CPU / 9.6
GB, MongoDB 21 % CPU / 24 GB; `oom_kill` still 1 (F53); chunk budget 512
(F36); no container restarted since 21:37 on 10-28, the embedder since
10-26 22:30.

Chunking (F55): 19,583 of the window's 25,105 chunks are `simple_fallback`
(avg 221 tokens, max 711, 667 under 20 tokens) — 78 % of the window, the
`.doc` share; the hybrid chunks of the `.docx` files average 386 tokens.
1,581 Word backup copies `Sicherungskopie von … .wbk` went in as documents
of their own (F58, new).

Embedder (F29, F47): a 15-token request 15.7 s at 02:50 (20.9 s at tick
41); the queue stays embedder-bound at 3 chunks per document. Backend log
(126,060 lines): 7,163 "simple chunking fallback" warnings (F55), 4
"requires antiword" skips (F54), 8 "Could not read file" errors with 7
tracebacks (F57), 11 "Slow request", 0 "Query embedding failed", 0
timeouts, 2 HTTP 5xx (F56).

Search (F56, new): at 02:51:26 and 02:51:41 UTC two sentence probes got
HTTP 500 from `/api/v1/search/text` and `/api/v1/search/hybrid` —
`maxClauseCount is set to 1024` from Atlas Search. They were the fork
session's tick-42 probes (its run and mine shared the minute): the two
failing sentences have 28 and 26 distinct terms ("Ich kann mich auch nicht
erinnern, diesen Darlehensvertrag …", "Mit Kaufvertrag vom gleichen Tag hat
Herr Dr. Philip Aumüllner …"), its ten passing ones 9–23 terms; my twelve
probes (all under 24 terms) got through. Every term is sent with `fuzzy:
{maxEdits: 2}` (text endpoint without `prefixLength`), and the up-to-50
expansions per term reach the 1,024-clause limit at about 25 terms — a
pasted paragraph or a long rewritten agent query fails outright.

Retrieval probes (12): hybrid 12 answered / 0 errors, hit@1 3, hit@5 12,
hit@10 12, median 15.0 s; semantic 12 / 0, hit@1 3, hit@5 4, median
14.9 s — hybrid finds every sentence, semantic only four of twelve: the
`.doc` letters are three overlapping fallback chunks each (F55) and the
sentence's own chunk is diluted; each request spends ~15 s on the query
embedding (F13, F18).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 68 s, **correct**, Firmenbuchabfrage sources (four
   copies, F11).
2. Sentence lookup ("Da die Angelegenheit drängt bitte ich um möglichst
   umgehende Erledigung und verbleibe") — 100 s, **right and honest**: the
   sentence is a closing formula of dozens of Pittner letters; the answer
   says so, names two (`18-1hanser`, `27-1millik`, both `.doc` of this
   window) and describes their urgency context; the sampled letter is among
   the sources.
3. Title summary "12-7-02-av Tel Filiale Mondo_1" — 106 s, **correct**
   content (Mag. Niklfeld's Aktenvermerk on identifying the vehicles for a
   Besitzstörungsklage against the Mondo branch), but the document itself is
   not among the ten listed sources although the answer quotes it — five
   other "av Tel" memos are listed instead (F15).

Chat, hand-crafted:

4. "Was teilte Frau Mag. Lipowec Dr. Czinglar im Telefonat vom 08.02.2006
   in der Sache Bipa / Fitz mit, und wie soll es laut Aktenvermerk
   weitergehen?" (auto) — 81 s, 1 iteration, **correct and complete**: BIPA
   fulfils all Auflagen and files no new Kündigung or Klage for now, DI
   Bogad sees to it, the other side is to be told when done, three more
   months of payments then stop, a Sachverständiger may confirm the works,
   wait for Bipa's technicians; the `.doc` (`BI-183/08-02-06 AV.doc`,
   stored in this window) is the first source, the other nine are
   related BIPA memos of the same client (F5-type neighbours, correctly
   unused).
5. "Was wurde in der Besprechung mit Herrn Petz in der Sache Rauchbauer /
   Petz (CZ-945) zum Kaufvertrag, zur Wohnungseigentums-Partnerschaft und
   zur Grunderwerbsteuer besprochen, welche Unterlagen hat Herr Petz
   übergeben, und auf welcher Grundlage wird das Honorar berechnet?" (auto)
   — 110 s, **correct and complete** across all three fallback chunks of
   the memo (Gewährleistungs-Ausschlussklausel, WE-Partnerschaft
   inheritance, Wohnbauförderung and the GrESt base, Lohnzettel plus the
   Mitbewohner declarations, Staatsbürgerschaftsnachweis, Scheidungsvergleich,
   honorar on EUR 240.000 without the Wohnbauförderung); the memo appears
   three times in the ten sources (one entry per chunk, not collapsed, F15)
   next to Kaufvertrag templates.
6. "Welche Bedenken äußert der Notar im Muster 422 'Notariatsakt mit
   Identitätszeugen' (Dr. Friedrich Zerk, Klagenfurt), auf welche
   Bestimmung stützt er sie, und wer sind die Identitätszeugen?" (fast) —
   18 s, **correct**: missing Spezialvollmacht of Karl-Heinz Mogul, § 36
   Notariatsordnung, the two witnesses with addresses; the template exists
   as `U-422.dot` (WEKA, this window), `T422.DOT` (MANZ) and a `.docx` in
   `Notariatsakte`, all three cited together (F11).

New: F56 (long queries fail the text/hybrid search with Lucene's
maxClauseCount), F57 (OLE2 `.doc` renamed `.docx` and jurXpert exports
without the officeDocument relationship fail as "Could not read file"), F58
(Word `.wbk` backup copies indexed as documents). Recurring F10, F11, F13,
F15, F16, F18, F29, F36, F42, F47, F54, F55 — the chat answers were all
correct this tick, including a three-chunk `.doc` memo in auto mode; the
losses are on the search side (F56) and in the index's noise (F57, F58).
Not measurable: F7, F22, F34, F35, F44, F48.

#### Tick 42 addendum — 02:50–03:20 UTC, second pass on the same window (fork session)

Own measurements: 12,839 real documents in 3 h (71 per minute: 9,185
`.doc`, 1,581 `.wbk`, 1,517 `.docx`, 426 `.dot`, 86 `.msg`; p50 12 s, p90
23 s), 8 `error` (antiword rejections and four "Could not read" `.docx`
Abrechnungen), 0 timeouts, 0 kills, 0 `GET /health` timeouts, 3 search
requests all 200, `oom_kill` still 1 (F53); backend 0.5–2 % CPU / 46 GB,
embedder 424–565 tok/s hourly, GPU 0 %, a direct 10-token request 15 s.
`.wbk` check for F58: the index holds 1,629 `.wbk` documents; of the 25
newest, 21 have no `.doc` sibling in the index at all, 3 are byte-identical
to their sibling (the content-hash dedupe missed them, so the hash is not
computed on the normalised text), 1 differs by one character.

Chat, hand-crafted (fork, 02:52–03:00 UTC):

4. "Welche Darlehen haben Jennifer-Johanna Drouin und Katharina Würthner
   laut der Vereinbarung vom 12.6.2017 ihrer Mutter für den Kauf der
   Wohnung Waldrebengasse 1 gewährt, und wie hoch war der Kaufpreis?"
   (auto) — 76 s, **correct** (je EUR 35.000, Barkaufpreis EUR 180.866,64,
   Top 11/Stiege 3), with the Papierkorb draft of 02.06.2017 alongside the
   final version in the sources (F4/F15).
5. "Was wurde im Mietvertrag zwischen Nana Paliani und Matthias Burghardt
   (Chimanigasse 42, Korneuburg) vereinbart: Räumungsfrist, Umzugsort,
   Größe des Mietgegenstands?" (auto) — 142 s, **two of three**:
   30.06.2018 and the Gartenhaus correct, but "enthält keine spezifische
   Angabe zur Quadratmeterzahl" although the contract says "ca. 30 m²,
   2 Zimmer, Küche, Bad und WC" two sentences later — the simple-fallback
   chunk with the size was not among the passed ones and the absence was
   asserted (F22, F55); 30 k prompt tokens (F11).
6. "Wie erledigte der Ausschuss der Rechtsanwaltskammer Wien mit Schreiben
   vom 18.7.2005 (GZ 2005/4202) die Beschwerde von Dr. Klaus Gutmann gegen
   RA Dr. Till Hausmann, und was empfahl Dr. Hausmann?" (auto) — 78 s,
   **correct** (Hausmann has no such deed, recommends a certified copy; the
   Ausschuss sees the matter as settled and takes the file "aus der
   Evidenz"), four of six sources unrelated books (F5).

Two and two-thirds of three fork questions correct. Recurring in the
addendum: F4, F5, F11, F15, F22, F53, F55, F58.
### Tick 43 — 2026-10-30 05:50 UTC (window 02:50 → 05:50, 3.0 h)

Job (32.2 h since the 21:38 resume, 16 night slots): 46,221 of 296,435
files, +10,356 in the window (58/min) — **10,353 real documents** (p50
16.2 s, p99 37.7 s, 23,475 chunks: 7,829 `.doc`, 1,850 `.docx`, 261 `.dot`,
220 `.msg`, 165 `.wbk`, 22 `.rtf`), 14 errors (all `requires antiword`,
F54/F57 refined below), 3 duplicate skips, 0 timeouts. Database +10,347 documents / +23,478 chunks (totals 145,358 /
1,622,048); 0 of 300 sampled documents suspect OCR, 550 chunks under 20
tokens (F16), 219 over 512 with a maximum of 604 (F10, F36, F55); 0
duplicate groups by hash. **ETA 45.0 d** (127.6 d at tick 42, 107.6 d at
tick 41): `.msg` is now priced from 1,151 processed files instead of 697
and the lifetime rate rose to 24.2 files/min — an 80-day swing within six
hours (F42).
Containers: backend 1.0 % CPU / 46.6 GB, embedder 100 % CPU / 9.6 GB,
MongoDB 12 % CPU / 24 GB; `oom_kill` still 1 (F53); chunk budget 512
(F36); no container restarted since 21:37 on 10-28, the embedder since
10-26 22:30.

Chunking (F55): 18,665 of the window's 23,521 chunks are `simple_fallback`
(79 %), the hybrid chunks of the `.docx` files average 373 tokens.

Readers (F54, F57): the 14 rejected `.doc` files are not antiword's fault —
11 are valid Word 97 files with two characters of text (22,528 bytes each,
`fcMac - fcMin = 2`: the `Grundbuch - <Datum>.doc` / `GBAuszug KG … EZ
….doc` exports of three case files and the Pittner letterhead; antiword
exits 0 with an empty result), `ws1417873000.DOC` is a `.docx` zip and
`ERV-Mahnklage.doc` is RTF. The message "legacy .doc format requires
antiword" is wrong in every one of the 14 cases.

Embedder (F29, F47): a 15-token request 16.5 s at 05:50 (15.7 s at tick
42). Backend log (126,735 lines): 6,226 "simple chunking fallback" warnings
(F55), 26 "requires antiword" lines (F54), 52 "Slow request" and 2 "Query
embedding failed" (the fork session's tick ran in the same minutes and
doubled the probe load), 13 tracebacks (the reader errors), 0 timeouts.
F56 recurred at 05:53 UTC: one of the fork's probe sentences (27 terms)
got `maxClauseCount is set to 1024` from `/search/text` and
`/search/hybrid`, and a second probe a 504 after the 30 s budget (F18);
my own probes stayed under 24 terms.

Retrieval probes (12): hybrid 12 answered / 0 errors, hit@1 5, hit@5 7,
hit@10 9, median 15.6 s; semantic 11 / 1 error, hit@1 3, hit@5 4, median
16.5 s — weaker than tick 42 (hybrid hit@5 12 then): three sentences of
`.doc` letters landed outside the top five under the doubled load, the
semantic side still finds only a third of the fallback-chunked letters
(F13, F18, F55).

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 85 s, **correct**, Firmenbuchabfrage sources (four
   copies, F11).
2. Sentence lookup ("Betrifft: Konkurs Peter Capek 11S 111/05t LG Wiener
   Neustadt") — 103 s, **right and honest**: the Betreff heads several
   Kurzbriefe of `P-43-06` / `P-43B-06` (2006–2008); the answer names them,
   explains the Konkurs context and lists the sampled letter among the
   sources.
3. Title summary "Vergleich_Unterhalt" — 105 s, **correct**: it merges the
   three documents of that title (`CZ-1925` `.docx` and its webErv PDF,
   `CZ-1543` prätorischer Vergleich), says so, and summarises Obsorge,
   Betreuung and Unterhalt; the sampled document is among the sources.

Chat, hand-crafted:

4. "Welche vier Punkte hat laut Aktenvermerk vom 27.8.2009 in der Sache
   Lichtenberger / Ehescheidung die Ehefrau handschriftlich zum
   Scheidungsvorschlag angemerkt, und was hat sie für ihren Polo bezahlt?"
   (auto) — 116 s, 2 iterations, **half right**: points 1 (Unterhalt,
   Systemaufstellung) and 2 (old car, Ablöse) and the Polo price of
   € 4.500,00 are correct, but the answer states the wife noted "zwei
   wesentliche Punkte" although the question said four and the memo lists
   four — points 3 (re-tiling the flat only with the husband's consent, no
   Investitionsablöse for mere embellishment) and 4 (the Vorausempfänge
   list) sit in the memo's later fallback chunks that were not passed, and
   their absence was asserted (F22, F55). The `.doc` (`CZ-1999/27-08-09
   AV.doc`, stored 03:46 UTC) is the first source, six sibling memos of
   the same case follow (F15).
5. "Was teilte Dr. Thomas Pittner dem Zagreber Rechtsanwalt Mirko Bogdanovic
   mit Schreiben vom 30.06.1999 betreffend die Active Reiseservice Kalman
   GmbH mit?" (fast) — 43 s, **correct**: Vollmacht dissolved because of the
   client's conduct, a last demand to the former client to settle
   Bogdanovic's costs, please keep me informed before filing; the `.doc`
   (`Pittner Archiv/TAG99-1.HJ/30-6bog.doc`) is the first source, the
   other nine are Active/Riviera letters of 1998–2000 (F5-type
   neighbours).
6. "Welche Bemessungsgrundlage, welchen Abrechnungszeitraum und welche
   Honorarnotennummer nennt die Kostenabrechnung an die Zürich Versicherung
   in der Sache Zaharic gegen Kuratorium Wiener Pensionisten-Wohnhäuser,
   und wie hoch ist die Gesamtsumme nach Nachlass und Umsatzsteuer?" (auto)
   — 174 s, 2 iterations, **three of four right, one wrong absence**:
   € 21.800,00, "bis 26.08.2021" and Honorarnote Nr. 21/116 are correct and
   the answer even separates the final note from the drafts `6.docx` /
   `7.docx` ("ENTWURF", earlier periods); but "eine Gesamtsumme … wird
   nicht explizit aufgeführt" is wrong — the table ends with **Gesamtsumme
   1.778,18** (Kostensumme 1.836,90, Nachlass 20 %, USt 295,56) in the
   fourth of the `.docx`'s four hybrid chunks, which the ten chunk-level
   sources (the same case's drafts, letters and the PDF copy of the note)
   crowded out (F22; the Honorarnote is a `.docx`, so not F55). Longest
   answer of the loop so far at 174 s (F13).

New: none. Recurring F10, F11, F13, F15, F16, F18, F22, F29, F36, F42,
F47, F54, F55, F56, F57 — the pattern of ticks 40–43 holds: retrieval
finds the right document first, but a multi-chunk document loses its later
chunks to its own siblings and the answer then asserts an absence (two of
six questions this tick). Not measurable: F7, F34, F35, F44, F48.

#### Tick 43 addendum — 05:50–06:20 UTC, second pass on the same window (fork session)

Own measurements: 10,396 real documents in 3 h (58 per minute: 7,858
`.doc`, 1,850 `.docx`, 275 `.dot`, 223 `.msg`, 165 `.wbk`; p50 16 s, p90
23 s), 14 `error` (antiword rejections of Grundbuch `.doc` extracts, F54),
0 timeouts, 0 kills, 0 `GET /health` timeouts, 3 search requests all 200,
`oom_kill` still 1 (F53); backend 0.8–1.7 % CPU / 46.6 GB, embedder
441–538 tok/s hourly, GPU 0 %, a direct 10-token request 24.7 s; chunks
up to 575–604 tokens (F55). ETA 44–45 d now that `.msg` has a window pace
of 1.8/min (F42).

Chat, hand-crafted (fork, 05:52–06:05 UTC):

4. "Worum ging es in der Besprechung von Dr. Michael Czinglar mit den
   Ehegatten Mader am 13.6.2005 zur Weinschenke Schlosskeller in
   Bisamberg, und welcher Kaufpreis wurde voraussichtlich genannt?" (auto)
   — 131 s, **correct in substance** (sale of the Schlosskeller business to
   Peter Prinz, the Resch purchase and the sub-lease to Elisabeth
   Mader-Cede, the Mietzins/Weitergabe advice), the EUR 45.000 price cut
   off by the answer length; 30 k prompt tokens (F11). The sources list
   shows two `.msg` titles with a trailing NUL (`"Piltz\x00"`,
   `"Pachtvertrag Prinz\x00"`) — F59, new.
5. "Wie hoch war laut dem Schreiben vom 23.4.1999 an die Zürich Kosmos
   Versicherungen AG im Regress gegen Svetozar Novakovic die noch offene
   Forderung, und worum ersuchte die Gattin des Verpflichteten?" (auto) —
   107 s, **correct** (ATS 87.142 inkl. Zinsen; Vergleich 23.2.1983 ATS
   100.000 + 4 % + 5.381,92; 90.100 paid; the wife asks for a Nachlass),
   plus the follow-up letter of 06.05.1999 (ATS 87.500) from the same
   archive — six sources all Novakovic letters.
6. "Was teilte Dr. Thomas Pittner Herrn Günter Gruber am 23.4.2001 zum
   Kaufvertrag EZ 1951 KG 63119 St. Peter mit, und welche steuerliche
   Befreiung wurde für den Schenkungsvertrag vom 31.1.2000 erwähnt?"
   (auto) — 166 s, **correct** (Abrechnung, Schließungssaldo to the CA
   account, Schenkungssteuerbefreiung § 15 ErbStG, the Wüstenrot
   Treuhandauftrag), but five of six sources are an unrelated 2008
   Kaufvertrag in four copies (F5, F15).

Three of three fork questions correct in substance. New (addendum): F59.
Recurring in the addendum: F5, F11, F15, F42, F53, F54, F55.
### Tick 44 — 2026-10-30 08:50 UTC (window 05:50 → 08:50, 3.0 h)

Office hours began at 07:00 UTC: `File slots: 8 of 16 (office hours, load
profile office)` and `Power plan desired: capped` in the log, the job
status shows `office_hours` / 8 slots. Job (35.2 h since the 21:38 resume):
55,691 of 296,435 files, +9,470 in the window (53/min; 16 slots for the
first hour, 8 after) — **9,477 real documents** (p50 11.3 s, p99 33 s,
20,968 chunks: 7,176 `.doc`, 1,370 `.docx`, 568 `.wbk`, 218 `.msg`, 127
`.dot`, 15 `.rtf`), 3 errors (2 `requires antiword`, 1 `.docx` Docling
failed on `Fremdgeldeingang.docx`, F54/F57), 3 duplicate skips, 0
timeouts. Database +9,444 documents / +20,895 chunks (totals 154,821 /
1,642,983); 0 of 300 sampled documents suspect OCR, 848 chunks under 20
tokens (F16), 389 over 512 with a maximum of 649 (F10, F36, F55); 0
duplicate groups by hash. ETA 72.7 d (45.0 d at tick 43, 127.6 d at tick
42) — the office-hours slot halving moved the lifetime rate to 26.6
files/min and the estimate by +28 days within three hours (F42).
Containers: backend 3.0 % CPU / 46.7 GB, embedder
100 % CPU / 9.6 GB, MongoDB 31 % CPU / 24.4 GB; `oom_kill` still 1 (F53);
chunk budget 512 (F36); no container restarted since 21:37 on 10-28, the
embedder since 10-26 22:30.

Chunking (F55): 17,217 of the window's 20,945 chunks are `simple_fallback`
(82 %); 568 more `.wbk` backup copies (F58).

Embedder (F29, F47): a 15-token request 14.6 s at 08:50 (16.5 s at tick
43) — unchanged by the slot halving, the queue is embedder-bound either
way. Backend log (154,446 lines): 8,069 "simple chunking fallback"
warnings (F55), 4 "requires antiword" (F54), 2 "Could not read file"
(F57), 58 "Slow request", 6 "Query embedding failed", 2 request timeouts
after 30 s (`/search/semantic`, `/search/hybrid`, F18), 4 HTTP 5xx, 11
`maxClauseCount` lines = 4 failed searches (F56, the fork's probes), 3
tracebacks, 0 timeouts.

Retrieval probes (12): hybrid 12 answered / 0 errors, hit@1 4, hit@5 7,
hit@10 7, median 14.0 s; semantic 12 / 0, hit@1 3, hit@5 4, hit@10 5,
median 14.1 s — the weakest hybrid round of the Office stretch (five
sentences of `.doc` letters outside the top ten), the semantic side
unchanged at a third (F13, F18, F55); 1 boilerplate sentence skipped
while choosing probes.

Chat, scripted (agent mode auto):

1. Control ALPO CONSULT — 71 s, **correct** answer, but the first five
   listed sources are now four copies of `E_R_.pdf` (the same ER print in
   `F-BlatLj_GeweR` and `TF-4-21`, F11) and a Honorarnote; the
   Firmenbuchabfrage files that led the list in every earlier tick are
   further down — the FN identifier path still pins them, the display
   order changed.
2. Sentence lookup ("Am 28.1.2008 kommen die Ehegatten Bousfield zu mir in
   die Kanzlei …") — 84 s, **correct** document (`CZ-1030/28-01-08 AV.doc`,
   first source) and content (Ehescheidung Bousfield, 30-minute meeting);
   the other sources are later memos of the same case (F15).
3. Title summary "18-2-03 Bf Dr Klose" — 102 s, **correct** (letter of
   18.02.2003 on the Bauendabrechnung "Neues Leben", the Schlichtungsstelle
   hearing of 24.01.2003), the document second in the sources behind an
   unrelated PVA filing and next to two "Unsortierte Dateien" hits (F15).

Chat, hand-crafted:

4. "Wer ist laut Grundbuchsauszug vom 16.12.2014 Eigentümer der
   Liegenschaft EZ 637 KG 01004 Innere Stadt, wie groß ist das Grundstück
   505, und welche Adresse hat es?" (auto, an EZ+KG identifier query) —
   66 s, 1 iteration, **correct**: Benedictiner-Stift Seitenstetten 1/1,
   614 m² with the 509/81/24 split, Franz-Josefs-Kai 29 / Morzinplatz, "Der
   Kleine Seitenstettenhof", the Denkmalschutz entry of 2009 and the
   A2 servitudes; one slip — "Kaufvertrag datiert auf den 15.07.1927" reads
   the B-sheet line `Stand 1927-07-15 Kaufvertrag 1834-02-15` as one date
   (the Stand is 1927, the Kaufvertrag 1834). The identifier path pinned
   the `.htm` exports of the same excerpt (`ME-2-13/… 16.12.2014.htm`, three
   chunks, and three later dates from `SE-1-15`, chunk-level entries, F15);
   the `.doc` of this window carries the same text and was not needed.
5. "Was übermittelte Dr. Czinglar Frau Mag. Helene Greindl mit Schreiben
   vom 14.03.2007 betreffend den Kaufvertrag Kreuzgasse 9 in Leobendorf,
   und worum ersucht er sie noch?" (fast) — 13 s, **correct** (the draft
   Kaufvertrag with her brother, please name the Kaufpreis; the
   Baulandbestätigung is his own step, rightly not listed); the `.doc`
   (`KCZ-231/14-03-07 bf greindl.doc`) is the first source, followed by the
   case's memos (F15). Fastest answer of the loop.
6. "Woran erinnert Dr. Czinglar Frau Mag. Lipowec von der Billa AG im
   Schreiben vom 18.12.2001 zur Mietvertragsbeendigung Jaklin /
   Mürzzuschlag, und welche Mietzinsrefundierung muss Herr Jaklin bei der
   Übergabe zahlen?" (auto) — 91 s, **correct and well sourced**: the
   reminder, S 10.000,-- plus USt as a year's rent, the Erlagschein at the
   handover, plus correctly attributed context from the sibling letters
   `1-10-jaklin` (mietzinsfrei from 01.10.2001, dissolved per 31.12.2001)
   and `29-8-billa`; the sources also list `BI-89/Sicherungskopie von
   Vereinbarung.wbk` directly next to `BI-89/Vereinbarung.doc` — F58 in a
   live answer.

New: none. Recurring F10, F11, F13, F15, F16, F18, F29, F36, F42, F47,
F54, F55, F56, F57, F58 — six of six chat answers correct (one date slip),
including the first EZ/KG identifier question of the loop; the
office-hours slot halving did not change the pace of the embedder-bound
Office stretch, only the ETA (F42). Not measurable: F7, F22, F34, F35,
F44, F48.

#### Tick 44 addendum — 08:50–09:20 UTC, second pass on the same window (fork session)

Own measurements: 9,486 real documents in 3 h (53 per minute: 7,187 `.doc`,
1,370 `.docx`, 568 `.wbk`, 219 `.msg`, 127 `.dot`; p50 11 s, p90 21 s), 3
`error`, 0 timeouts, 0 kills, 0 `GET /health` timeouts; office profile 8
slots since 07:00 UTC (the pace did not drop: the embedder, not the slots,
sets it); backend 1.8–3.8 % CPU / 46.7 GB, MongoDB 90 % CPU (the
duplicate-hash and registry lookups of 50+ files a minute), embedder
366–527 tok/s hourly, GPU 0 %, a direct 10-token request 14.5 s; 69 search
requests 200, 3 × 500, 2 × 504, 6 query-embedding retries. ETA 72.7 d
(`.docx` window pace 0.27/min from a handful of files, F42).

Chat, hand-crafted (fork, 08:52–09:00 UTC):

4. "Welche Biomarker kauft die CENTOGENE AG laut dem Vertrag mit der
   pharm-analyt Labor GmbH, und bis wann muss Hermann Mascher als Coautor
   genannt werden?" (auto) — 80 s, **correct and complete** (Lyso-Gb1,
   Lyso-Sphingomyelin/Stoff 509, Lyso-Sulfatid; Lyso-Gb3 without patent;
   co-author until at least end of 2017), from the `.docx` stored at
   07:32 UTC plus the signed PDF version of the same contract in the
   sources (F15).
5. "Worauf wies Dr. Thomas Pittner die NIDUS Hausverwaltung im Schreiben
   vom 12.02.2003 zum Vorzugspfandrecht hin, und welches Urteil erwähnte
   er?" (auto) — 89 s, **correct** (Betriebskostenrückstände not older than
   six months; the Urteil of 27.9.2002 in 2 C 7/01w BG Korneuburg), with
   § 27 WEG 2002 context from another matter's filings; the asked letter
   is cited in the text but absent from the sources list (F15).
6. "Was vereinbarte Dr. Michael Czinglar laut Aktenvermerk vom 22.8.2016
   mit Herrn Mayer zur Honorarfrage?" (auto) — 54 s, **correct** (no
   payment for now, Mayer reports back when his finances recover; the
   worthless share package), the first source the memo itself.

Three of three fork questions correct. Recurring in the addendum: F15, F42.
