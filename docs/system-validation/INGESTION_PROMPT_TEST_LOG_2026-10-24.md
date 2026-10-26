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
| F1 | high | **Throughput collapsed 20× after the restart with concurrency 16, and timed-out conversions keep running.** Inserts fell from ~2,000 documents per 30 min (07:30–08:30, `max_concurrent_files` 2) to ~100 per 30 min after 08:55 (`max_concurrent_files` 16, `thread_pool_workers` 16). In 2.1 h the job logged 209 per-file timeouts at 902 s, but 16 slots × 126 min cannot even hold 209 × 15 min, and the longest logged conversion took 2,160 s against a 902 s limit. 211 timeouts fell into the single hour 10:14–11:14 UTC. Tick 2: not one timeout between 11:02 and 13:50, and inserts recovered to 1,500–1,800 per 30 min from 12:30 on, once the batch of ~170 heavy scanned Grundbuch/Firmenbuch extracts and their zombie retries had drained. So the collapse is triggered per batch of slow files rather than permanent, but every such batch costs hours and the mechanism is unchanged. The timeout is `asyncio.wait_for` around `run_in_executor` (`src/ingestion/ingest.py:3939`): on timeout the slot is released and the file is queued for a retry with a 1.5× budget, but the thread keeps converting, so more than 16 conversions run at once, every file slows down, more files time out, and the retries redo the work. The API process sits at ~1,350–1,500 % CPU with 28–31 GB RSS. ETA from the status endpoint: 26.8 days at tick 1, 13.1 days at tick 2 (at the pre-restart rate it would be ~3.5 days). Fix: run conversions in killable worker processes (the `process_isolation_enabled` flag is stored but nothing in `src/ingestion` reads it), or make the per-file budget scale with concurrency / measure CPU time, and cap concurrency at what the CPU can serve (20 cores in the container; `OCR_OMP_THREAD_LIMIT` is empty). Tick 3: one timeout in 3 h (905 s), 30 files/min, ETA 8.1 d. Tick 4: one timeout, 31 files/min, ETA 7.8 d. Tick 6 (after the resume, F23): 553 timeouts on 451 files in 4 h, 18 files/min, ETA 13.7 d, the same slow scans as on day one. Tick 7: the retry tail drained, 5 timeouts in 3 h, 18 files/min, ETA 13.0 d. Tick 8: 0 timeouts, 19 files/min, ETA 12.2 d. Tick 9: 10 timeouts, 18 files/min, ETA 12.4 d. Tick 10: 1 timeout, 17.8 files/min, ETA 12.7 d. Tick 11: 1 timeout, 17.5 files/min, ETA 12.8 d. Tick 12: 5 timeouts, 17.0 files/min, ETA 13.1 d. Tick 13: 6 timeouts, 16.4 files/min, ETA 13.4 d. Tick 14: 2 timeouts, 15.8 files/min, ETA 13.8 d. Tick 15: 9 timeouts, 15.3 files/min, ETA 14.2 d. Tick 16: 8 timeouts, 14.7 files/min, ETA 14.6 d (the rate has fallen every tick since tick 8). Tick 17: 11 timeouts, 14.1 files/min, ETA 15.2 d. Tick 18: 19 timeouts on 16 files (Energieausweis scans among them), 13.7 files/min, ETA 15.6 d. Tick 19: 23 timeouts on 19 files, 13.2 files/min, ETA 16.1 d; the backend now idles at 0.5 % CPU while the embedder runs at 100 % (F29). Tick 20: 25 timeouts on 21 files, 12.6 files/min, ETA 16.8 d (277 documents per hour). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: process-isolated conversions killed on timeout (PR #25)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F2 | high | **Per-file stats and failures are only persisted when the job ends.** `_save_ingestion_stats` (`ingest.py:4139`) writes `ingestion_stats` and `failed_documents` after the loop, so for a multi-week job the failed-documents page, the analytics endpoints and the retry options (`retry_timeouts`, `retry_no_chunks`) have nothing to work with, and a restart loses everything accumulated in memory. The `rag_diejuristin` database has neither collection. Fix: flush `file_stats` in batches (every N files or minutes). Tick 5: the redeploy at 22:56 UTC discarded 14 h of in-memory stats (212 timeouts, ~700 no-chunk files) for good. Tick 6: because nothing was persisted, the resumed job re-queued every one of them (F23). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: stats and failed_documents flushed every 25 files / 60 s (PR #25)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 1, 5, 6 |
| F3 | medium | **Job status counters stay at zero.** `timeout_files`, `no_chunks_files`, `error_files`, `image_only_pdfs` and `failed_files` all read 0 in `GET /ingestion/status` and in the job document while the log shows 209 timeouts and 471 no-chunk files in the same window. `processed_files` (1,179) also counts no-chunk files and duplicates, so "documents 1,172" does not match the 319 documents actually inserted. Tick 5: after the resume the same fields read `null`. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: job counters follow the written stats (PR #25)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 1, 5 |
| F4 | medium | **Copies first seen in the same run are all ingested.** The content-hash de-duplication compares against `existing_hashes` loaded once at job start (`ingest.py:3718`); hashes of files processed during the run are never added, so every copy that was not already in the database gets converted, embedded and stored. Tick 1 window: 36 duplicate groups, 65 redundant documents out of 319 (20 %); today overall: 2,007 groups, 2,974 redundant documents. Besides the wasted OCR, the copies crowd search results (the control question returned `Gelöscht/…` and the live copy of the same file). Tick 2: 477 groups / 710 redundant documents out of 4,231 (17 %). Fix: add the hash under the results lock after each successful file; show copies as one result with several locations. Tick 3: 1,268 groups / 1,845 redundant of 8,400 (22 %); the chat's 10 source slots held only 2 distinct files for the Honorarnote and KSV questions. Tick 4: 904 groups / 1,278 redundant of 5,878 (22 %). Tick 5: 890 / 1,233 of 4,824 (26 %). Tick 6: 434 / 625 of 2,894 (22 %). Tick 7: 502 / 796 of 3,291 (24 %). Tick 8: 458 / 673 of 3,606 (19 %). Tick 9: 434 / 658 of 2,923 (23 %). Tick 10: 404 / 573 of 2,623 (22 %). Tick 11: 380 / 554 of 2,783 (20 %). Tick 12: 386 / 554 of 2,357 (24 %; one letter stored 12 times across client folders). Tick 13: 291 / 436 of 2,073 (21 %). Tick 14: 302 / 427 of 1,946 (22 %). Tick 15: 219 / 311 of 1,761 (18 %). Tick 16: 216 / 307 of 1,585 (19 %); three copies of one template file filled 9 of the 10 source slots of a chat answer. Tick 17: 191 / 309 of 1,307 (24 %); one scan (`SKMBT_C224e18041813150.pdf`) stored in four folders. Tick 18: 197 / 300 of 1,300 (23 %). Tick 19: 165 / 236 of 1,121 (21 %). Tick 20: 128 / 195 of 832 (23 %). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: same-run copies deferred and skipped (PR #25)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F5 | medium | **Lookup by document title or file name fails.** The text search index covers only `chunks.content` (`dynamic: false`); `metadata.title` and `metadata.source` exist on every chunk but are not indexed, and the chat has no title-match stage. "Fasse das Dokument "Grundbuchabfrage, KG 11029, EZ 52" zusammen" returned "nicht enthalten" although three copies exist; `/search/text` and `/search/hybrid` for the title return unrelated documents. Fix: index `metadata.title` and `metadata.source` (with a path analyzer) in `text_index`, boost title matches, or prepend the title to the contextualized chunk text. Tick 3: "Sonstige Erledigung, Beschluss-1-3" not found, a different "Protokoll, 2-3" summarised instead. Tick 4: the ERV file name "SonstigeErledigung_19058_91561_2018_03_29" not found either. Tick 5: `MagdicsBallLi_PetrPe/DZ, Aktenbestandteil, Klagsrückziehung.pdf` is not found by its title words either; its key figures ("wegen: 3 EUR 13.340,14", a Kostenverzeichnis of label/value fragments) are split across chunk boundaries, so neither text nor vector search surfaces it. Tick 7: "Vorbereitende Schriftsatz der beklagten Partei_55500" not found, the agent summarised "similar procedure types" instead. Tick 8: "Ladung_21921_106616_2021_01_12" not found, honest refusal. Tick 9: "DZ, Beilage, 28Cg 34_14x - Replik Blg_2" not found, and this time the agent summarised the sibling "DZ, Sonstiges, Schriftsatz-4" as if it were the requested document (F20). Tick 10: "Schummer_Kriwanek,_Das_neue_Unternehmensgese…" not found, honest. Tick 11: a counter-example, the ZVB 2020/27 article was found and summarised because its title words recur in the body; titles work only when they do. Tick 13: "Ärztliches Zeugnis" (a medical certificate for an adult-representation registration) not found; the agent described the document type from Vorsorgevollmacht templates instead, without saying the document itself was missing (F20). Tick 14: asked for "Direktzustellung_23198_114708_…", it summarised the ERV sibling "…114707" (the cover note of the same delivery) as if it were the requested file. Tick 16: a Nutzwertgutachten was "summarised" from the surveyor's cover letters and a later Wohnungseigentumsvertrag of the same matter, not from the Gutachten itself. Tick 17: "Klient Einheitssatz, Abrechnung bis 12_03_2019" not found; the agent said so and listed similar Einheitssatz Abrechnungen of other clients, with the disclaimer. Tick 19: "IMG_20210920_0005" (a phone scan) not found, honest refusal after 298 s. Tick 20: "Fotomontage samt E-Mail" was described from the Klagebeantwortung drafts that cite it as evidence, not from the document (F20). | 1, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 16, 17, 19, 20 |
| F6 | medium | **Unreadable files are stored as garbage text.** `M U S T E R/MANZ/manztexte/…/html/*.htm` (1999, 425 KB) is not HTML at all (high-bit bytes from offset 0, probably an obfuscated MANZ format); it is decoded with replacement characters, chunked and embedded, and it shows up in search results. 123 documents are more than 5 % U+FFFD; 563 documents contain at least one (500 PDFs, 63 htm). Fix: after decoding, treat text with more than a few percent replacement characters as unreadable (`no_chunks` with a clear reason), and log the `bs4.dammit` warning once per file. Tick 3: `KLEIN PS-CIORA/Attachements/Mahnschreiben-Ciora-Vasile.pdf` carries "����" in its letterhead. Tick 10, a variant without replacement characters: `Gesetzestexte/Europa/EU Gründungsvertrag.pdf` reads "GR˜NDUNG DER EUROP´ISCHEN GEMEINSCHAFT", a PDF whose font maps umlauts to the wrong glyphs; text search for "Gründung" cannot find it. | 1, 3, 10 |
| F7 | medium | **The API process is starved by in-process ingestion.** UI-started jobs run inside the backend container (`runner: api`); the worker container idles. Consequences in 2 h: 7 `Request timeout … GET /health`, one `504` on `/ingestion-queue/queue`, slow-request warnings for `/ingestion/status` and `/system/host-stats`, and 8 `Rate limit exceeded … api_ingestion` hits caused by the Ingestion page's own polling. The Docker health check (`curl -f /health`, 10 s, 5 retries) still passes but sits one busy minute away from marking the container unhealthy. Tick 2: RSS grew from 28.4 to 30.7 GB; no health-check timeouts in this window (no timeouts either, see F1). Fix: hand UI-started jobs to `backend/workers/ingestion_worker.py`, and exempt the UI's status polling from the ingestion rate limit. Tick 3: RSS 32.5 GB (28.4 → 30.7 → 32.5). Tick 4: 34.0 GB. Tick 5: 30.8 GB just before the redeploy, so it fluctuates rather than leaks monotonically. Tick 6: 35.3 GB only 4 h after the fresh start, so the bulk is reached quickly (zombie conversions and their page images). Tick 7: 39.0 GB, a new high 7 h after the restart. Tick 8: 38.0 GB. Tick 9: 42.0 GB, and `GET /health` itself hit the 30 s request timeout 4 times (HTTP 504 to the Docker health check, which tolerates 5 failures in a row before marking the container unhealthy). Tick 10: 37.0 GB. Tick 11: **44.4 GB**, the highest yet, at 1,435 % CPU; MongoDB at 131 % CPU in the same snapshot. Tick 12: 42.1 GB. Tick 13: 40.9 GB. Tick 14: 37.9 GB. Tick 15: 36.2 GB. Tick 16: 39.6 GB. Tick 17: **45.5 GB**, a new high 37 h after the restart. Tick 18: 44.2 GB. Tick 19: 40.3 GB. Tick 20: 37.5 GB, and `GET /health` timed out 4 more times. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: UI-started jobs are queued for the ingestion worker when one is alive (`INGESTION_UI_JOB_RUNNER`, default auto); pause/resume/stop reach it through `control_command`); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F8 | low | **OSD failures are logged at ERROR for near-blank pages** (235 in 2 h, "Too few characters. Skipping this page"). They are expected for cover sheets and blank scans; downgrade to DEBUG or count them per file. Tick 2: 3,796 in 2.8 h, 23 ERROR lines per minute. Tick 3: 4,553 in 3 h. Tick 4: 5,406. Tick 5: 6,315. Tick 6: 6,233. Tick 7: 7,120. Tick 8: 7,125. Tick 9: 6,757. Tick 10: 7,844. Tick 11: 7,708. Tick 12: 7,074. Tick 13: 7,075. Tick 14: 9,018 (50 per minute). Tick 15: 7,509. Tick 16: 7,827. Tick 17: 8,020. Tick 18: 7,740. Tick 19: 8,781. Tick 20: 7,543. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F9 | medium | **471 "No chunks created" warnings in 2 h; most are expected-empty files, but image-only scans are among them and vanish without a record.** Expected: Outlook `ATT0000x.txt` placeholders (38 + 23 + 20 + 19 …) and MANZ frameset pages (`i_stichwort`, `i_inhalt`, `suchen`, `searchbnd1`). Not expected: `EDELS-BAUM/Attachements/Kontoauszug Sparkasse OÖ 15_07_2020.PDF` is a one-page image-only PDF (4 image objects, no text layer, 595×420 pt) that Docling "converted" in 3.8 s with no text, i.e. OCR did not run on it or returned nothing; it is now neither a document nor a recorded failure (F2), so nobody will retry it. Fix: classify empty text/HTML files as "empty" without a warning; for PDFs with zero text and ≥ 1 image, force full-page OCR and, if that still yields nothing, record `image_only_pdf` in `failed_documents`. Tick 2: 33 no-chunk files, plus 15 stored documents under 200 characters that are image placeholders (`<!-- image -->` with a few OCR fragments, e.g. a floor plan, an SMS screenshot). Tick 3: 53 no-chunk files, 40 documents under 200 chars. Tick 4: 69 and 61. Tick 5: 56 and 42 (web-page prints such as `P-8-08/HANDY Parken.pdf` reduce to menu labels). Tick 6: 697 no-chunk files, 163 of them the Outlook `ATT0000x` placeholders processed a second time after the resume. Tick 7: 58 no-chunk files, 59 documents under 200 chars. Tick 8: 72 and 63 (photo collections such as `KaceFrVU/Bilder Teil 3.pdf`, `5. Fotos Wohnung_1`). Tick 9: 79 and 47 (a 3-character document: `WESA-ATLANTIS/Attachements/Kabelbrücke Beispiel 4.pdf` = "2.0"). Tick 10: 92 and 46 (an e-card scan, a birth certificate, an auction notice). Tick 11: 88 and 58 (a 47-character `Krankenakte.pdf`, a Geburtenbuch scan, a sketch). Tick 12: 70 and 45, among them three copies of a passport scan reduced to its machine-readable zone (`MANDL-KV/Attachements/Reisepass ….pdf`), i.e. identity-document data in the search index. Tick 13: 91 and 33. Tick 14: 92 and 54. Tick 15: 67 and 45. Tick 16: 69 and 35, plus another passport scan reduced to its machine-readable zone (`WlazAg_WlazPe/Ausweise.pdf`) and savings-book scans. Tick 17: 59 and 24. Tick 18: 118 no-chunk files (scanned court exhibits "DZ, Beilage …" that OCR to nothing) and 51 tiny documents, among them a residence-permit card (`FP-GLORI-VWSTRAF/Attachements/AUT.pdf`). Tick 19: 52 and 23. Tick 20: 29 and 17. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: pure scans are recorded as `image_only_pdf` (OCR found nothing) or `image_only_pdf: true` (OCRed), counters `image_only_pdfs` / `image_only_pdfs_ocr`, summary `image_only_pdfs_ocr_ok`; empty files log at INFO); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F10 | low | **Chunks exceed `max_tokens` after contextualisation.** 2,487 of 6,262 new chunks are 513–514 tokens (the heading context is prepended after the 512-token split). Harmless with the llama.cpp embedder (`-c 32768`), but the budget should include the context. | 1 |
| F11 | low | **Chat costs ~50 s and 13–17 k prompt tokens per question** (10 sources × ~435-token chunks into gemma4:12b, 1 iteration). Acceptable for "thinking", too slow for "fast". Also, the non-streaming `POST /sessions/{id}/messages` returns `thinking.search.operations` empty, so the searches the orchestrator ran are only visible in `agent_trace.orchestrator_steps`. Tick 3: fast mode answered the KSV question in 15 s versus 61 s in auto mode with the same core facts, but reports 0 tokens in `stats` and `agent_trace`. Tick 4: judgment question 22 s fast versus 103 s auto (2 iterations), same facts and the same wrong reasoning (F20). Tick 6: first clear divergence, fast mode (8 s) answered the expert-fee question with the case's Streitwert (EUR 34.000) while auto mode (78 s) was exactly right; fast mode's single search without reformulation misses the decisive document more often. Tick 7: fast mode correct on the deposit-refund e-mail (42 s under load versus 54 s auto). Tick 8: fast mode (55 s) claimed the GmbH guide "names no minimum capital" because it only received the guide's table-of-contents chunk, while auto mode (71 s) found the EUR 35.000 sentence. Tick 9: fast mode correct and richer than auto on the WEG-Novelle article, 42 s versus 205 s. Tick 10: fast mode right on five of six lease figures (48 s), auto mode on all six plus a date discrepancy between offer and contract (138 s). Tick 11: fast mode correct on a technical statement (37 s). Tick 12: fast mode (58 s) got total and instalments right but declared the Kautionsguthaben absent; auto mode (67 s) had all three and the acceptance conditions. Tick 13: both modes equal on the Honorarnote (both missed its amounts table), fast 83 s versus auto 138 s; auto mode took 274 s on the remediation offer. Tick 14: both modes equal again on the § 146 commentary (same absence claim), fast 98 s versus auto 112 s. Tick 15: fast 76 s versus auto 229 s (2 iterations) for the same header facts; the other auto question timed out at 300 s. Tick 16: both modes fully correct on the Urheberrecht template claim, auto 73 s, fast 131 s (fast is not always faster under load). Tick 17: both modes fully correct on § 157 UGB, fast 78 s versus auto 195 s. Tick 18: both hand-crafted auto questions timed out at 300 s; fast mode answered the air-conditioner opinion in 146 s with the core points (OGH 5 Ob 24/08b, the "fast notwendig" standard, location and shading alternatives) but without the § 16 WEG criteria list and the second decision. Tick 19: auto mode timed out on the lease question; fast mode (95 s) got the object and deposits but declared start, duration and rent absent. Tick 20: fast mode (192 s) honestly reported that only the opinion's heading was available, auto mode (152 s) answered with the opposite conclusion from another document; here the honest non-answer was the better one. | 1, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F12 | low | **Letter-soup OCR on some scans**, e.g. `M U S T E R/EHE/Berechnung Unterhalt Ehegatte_Kinder.pdf` → "PP Non: 2 FU 9 fu tte 72 7 ImenDgur vavadig rey piadmyo". A per-document quality score (vowel-less long tokens, alphanumeric share) would let the UI flag such documents for re-OCR at a higher DPI. Tick 2: `P-4-12/webErv/2012-11-02 Beilage B.PDF` ("elenbechen Allardrm ZI T8 Terrace eo"). Tick 3: a CAD plan (`P-76-11/Pläne Elk/Einreichplan …`) and a Meldezettel scan. Tick 4: `FehrSt_GomeMa/Unterlagen/Kostenvoranschlag S_2.pdf` ("ae tr? RE”"). Tick 5: `Metka_SchwLu/DZ, Beilage, 28Cg 34_14x - Replik Blg_5.pdf` ("Ayyesadoysod adeL TL OTOZ'TT'ST", an upside-down scan), `WeisJo_HaibMa/Weiss Unterlagen1.pdf`. Tick 6: handwritten notes (`SperWo_Unter/sperlich _2_.pdf`, `SandEl_SETür/Vermerk Besprechung.pdf`, `ErseYu_Taxi/Erses.pdf`) become word salad; a handwriting detector could mark them "handschriftlich" instead of indexing noise. Tick 7: a handwritten testament (`P-51-18/Attachements/Scan0190.pdf`, 155 chars of noise) and a client-meeting note (`SchaMa_RomaOl/Konf_ mit Mandanten.pdf`), documents a lawyer would want flagged rather than silently unreadable. Tick 8: `SeebJo_GarzKa/1550818581655.pdf` came out as one letter per line (vertical or rotated text layout, not OCR noise). Tick 9: bank statements scanned upside down come out as mirrored digit strings ("00'0 €6'999 00'0 95'259", `F-Erzbis_Covid19/Albin Denk/Mietzinse Kontoauszüg EG 3.pdf`, `ER-2-20/Attachements/SKMBT_22321051213220.pdf`), so the OSD rotation step did not catch them; plus a sick note and a vehicle-disposal confirmation as letter soup. Tick 10: handwritten notes again (`ErdiAl_ES/Erdinc Notizen.pdf`). Tick 11: two more upside-down bank statements (`ERZBI-WEIDI/…/SKMBT_22321080912540.pdf`, `ER-2-20/…/SKMBT_22320082010120.pdf`) and a cellar plan. Tick 12: 13 of 300 sampled, incl. another upside-down statement, a vehicle registration and a creditor list. Tick 14: 8 of 300, incl. an upside-down invoice scan (`HIRSC-PAAR/Attachements/IMG_20220102_0001.pdf`) and a key-handover protocol. Tick 15: handwritten client-meeting notes (`SchaCh_Scheidung/…/Scharf - Mitschrift Dr_ Trautmann.pdf`, `P-37-05/1988-08-04_KONFL_m_Mdt.pdf`) and a hand-filled custody calendar. Tick 16: `HollPe_Uniqa/Unterlagen/1621923533865.pdf` is 16,149 characters of one letter per line (a rotated or columnar layout read letter by letter), i.e. 16 k characters of unsearchable text embedded as if it were prose; plus two copies of a mine plan as digit soup. Tick 17: 10 of 300, incl. a handwritten accident report and another passport copy (`BÖHM-KV/Attachements/Pass Pale Kopie.pdf`). Tick 18: 9 of 300, mostly plans (an HKLS building-services plan, house plans) and an upside-down scan. Tick 19: 10 of 300, again plans and price lists; a bachelor thesis was flagged on its table of contents only (heuristic false positive). Tick 20: 9 of 300, construction plans and two handwritten letters (`P-3-21/Beilagen/2021-07-13 Handschriftl Anzeige ….pdf`). | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20 |
| F13 | info | **Retrieval retention is fine for distinctive sentences**: hybrid hit@5 = 10/12, hit@1 = 6/12; semantic-only hit@5 = 5/12 (exact-sentence probes favour the text index). The two misses were form boilerplate shared by hundreds of Firmenbuch extracts; the probe picker now skips sentences found in ≥ 4 documents. Tick 2: hybrid 5 of 6 in the top 5 (6 of 12 probes were lost to F14), 12 boilerplate candidates skipped. Tick 3: hybrid 8 of 12 in the top 5 and 10 of 12 in the top 10, semantic 5 of 12. Tick 4: hybrid 7 of 9 answered probes in the top 5 (3 lost to 504s), semantic 2 of 6 (6 lost). Tick 5: only 2 probes answered (the rest hit the redeploy, F23); 1 of 2 hybrid, 2 of 2 semantic in the top 5. Tick 6: hybrid 6 of 9, semantic 6 of 10 (5 lost to 504s). Tick 7: hybrid 7 of 11, semantic 6 of 11; 16 boilerplate candidates skipped. Tick 8: only 4 hybrid and 6 semantic probes answered (the rest 504), 3 of 4 and 3 of 6 in the top 5. Tick 9: 3 answered per index, all at rank 1; 18 of 24 lost to 504s. Tick 10: 2 answered per index, 20 of 24 lost. Tick 11: 8 answered per index, 7 of 8 in the top 5 for both (6 at rank 1). Tick 12: hybrid 5 of 9, semantic 5 of 10 in the top 5. Tick 13: 18 of 24 lost to 504s, 1 of 2 and 1 of 4 answered in the top 5. Tick 14: 21 of 24 lost, the 3 answered all in the top 2; 21 boilerplate candidates skipped. Tick 15: **0 of 24 answered**, every probe search timed out. Tick 16: 0 of 24 again. Tick 17: 1 of 24 (semantic, rank 1). Tick 18: 0 of 24. Tick 19: 0 of 24. Tick 20: 0 of 24, the fifth blackout. | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F14 | medium | **The search API rate limit (30 requests per minute per client IP, `api_search` in `backend/core/security.py`) is too low for authenticated clients and is shared by everyone behind the LAN gateway.** Tick 2's probes received 18 HTTP 429 responses (`retry_after` 21 s) after 24 requests in a few seconds; every LAN user reaches the backend through the Caddy gateway's container IP, so one bucket serves the whole office unless the middleware honours the forwarded client address. Fix: key the limiter by user or API key when authenticated, read `X-Forwarded-For` from the gateway, and raise the search limit. The test script now waits and retries once on 429. Tick 12: the limiter stalled the probes three times (6–13 s waits). Tick 13: three more stalls. | 2, 12, 13 |
| F15 | medium | **Citations name only the document title, and titles are shared across cases.** 456 documents are titled "Sonstige Erledigung, Beschluss", 365 "Ladung, Ladung zu einem bestimmten Termin", 30 "Sonstige Erledigung, Kostenbestimmung für Drittschuldnererklärung". The tick-2 title-summary question was answered from `StahRu_Darlehen/…` although the window's document of that title was `DohnEl_Todic/…`, with no hint that 30 such documents exist; the follow-up question listing them attributed them to titles like "SonstigeErledigung_14800_67943_2015_07_30" instead of the case folders. Fix: include the case folder (first path segment, the Akt) in `[Source: …]` citations and in the sources list, and let the agent say when a title is ambiguous. Tick 4: "Sonstige Erledigung, Urteil, 2" exists in `11_FeiBad_1/` and `Zürich_AnsaEv/`; the answer mixed both under one citation (F20). Tick 5: asked for "the" Vorschreibung of Geylinggasse 11, the agent answered from one of 49 unit ledgers in the case folder without saying so (correct for that unit, 383,99, but the asked-about unit pays 284,43). Tick 12, a positive example: asked to summarise "Bescheid über die Festsetzung von Aussetzungszinsen", the agent said that several such Bescheide of different taxpayers exist and listed each with its own period and amount, which is the behaviour this finding asks for; it still had no way to name the case folders. Tick 15: "Unfallbericht" (a generic title) was answered as a survey of several accident reports, again without saying which case each belongs to. | 2, 3, 4, 5, 12, 15 |
| F16 | low | **Tiny chunks from form layouts.** 1,214 of 32,795 new chunks (3.7 %) are under 20 tokens (one form label per paragraph: "Gericht:", "am:", "Beginn:"); they produce weak embeddings and pad the vector index. Fix: merge sub-20-token chunks into their neighbours (HybridChunker `merge_peers`) or drop label-only chunks. Tick 3: 1,853 of 74,283 (2.5 %). Tick 4: 1,589 of 63,808. Tick 5: 968 of 56,441 (1.7 %). Tick 6: 628 of 38,132. Tick 7: 1,097 of 44,734. Tick 8: 1,092 of 45,953. Tick 9: 766 of 39,264 (2.0 %). Tick 10: 702 of 40,736. Tick 11: 758 of 39,144. Tick 12: 659 of 35,056. Tick 13: 911 of 34,440 (2.6 %). Tick 14: 639 of 33,824. Tick 15: 502 of 28,566. Tick 16: 714 of 28,395 (2.5 %). Tick 17: 429 of 25,481. Tick 18: 429 of 26,451. Tick 19: 395 of 22,594. Tick 20: 318 of 19,072. | 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F17 | medium | **Case numbers (Aktenzeichen) are tokenisation-sensitive and the agent fills the gap with another case.** Asked about "Strafsache 13 U 39/16v" (typed without the space the court prints), hybrid search does not return the hearing protocol `CZ-1835/webErv/SonstigeErledigung_16092_75279_2016_04_13.pdf` in its top 10; with "13 U 39/16 v" it is rank 4 (text search rank 2). The agent then answered from the sibling cover letter and presented cost-ledger lines dated 15.03.2016 from an unrelated case (`StahRu_VU/Stahl - VU HN.pdf`) as supporting details. Fix: extract case numbers at ingestion into a normalised keyword field (spaces removed) and expand queries both ways; when a question names an Aktenzeichen or case folder, restrict synthesis to documents of that case and say when nothing matched. Tick 10: "4 Cg 65/21b" (spelled as printed) retrieved the case's Folgeeingaben, Protokoll and ERV receipts but not the Schriftsatz with the Streitwert; the agent said so instead of guessing. Tick 11: "14 C 995/23z" and "KG 01660 EZ 858" (both as printed) retrieved the right filings and the answers were complete, so the failures are specific to spelling variants and to form fragments, not to identifiers as such. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: identifier-aware retrieval (PR #28)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 2, 10, 11 |
| F18 | high | **Search latency climbs to 7–13 s while ingestion embeds.** Query embeddings go through the same llama.cpp server (`embedder-gpu`, `-np 4`, 8,192-token slots) as the ingestion batches of 100 chunks; at tick 3 every slot was `is_processing`, semantic search took 8.5–13.0 s server-side (text search 47 ms; 0.6 s at tick 2), hybrid probes had a median of 7.3 s, and chat latency rose to 60–68 s. Fix: a dedicated slot or second embedder instance for interactive queries, smaller ingestion batches (`embedding_batch_size`) while users are active, or a priority queue in `src/ingestion/embedder.py`. Tick 4: hybrid median 16.7 s, semantic 23.8 s, and 9 searches (6 semantic, 3 hybrid) were cut off by the backend's 30 s request timeout (`REQUEST_TIMEOUT_SECONDS` in `backend/main.py`) with HTTP 504; chat 83–103 s. Search is effectively unavailable while ingestion embeds. Tick 5: 4 more 504s in the minutes before the redeploy. Tick 6: medians 16.3 s hybrid / 15.1 s semantic, 5 × 504; the control question failed for the first time because the Firmenbuchabfrage was not among the sources. Tick 7: medians 11.5 s hybrid / 7.4 s semantic, 2 × 504, chat 110–135 s. Tick 8 (worst so far): 14 of 24 probe searches hit the 30 s limit, medians 30.0 s / 27.9 s, chat 48–111 s. (The single `OpenAI API error: 401` in `backend.routers.system` at 06:31 UTC is a provider test from the System page with the empty OpenAI key, not the embedder.) Tick 9: 18 of 24 probe searches timed out, medians 30.0 s for both indexes; scripted chat 62–129 s, hand-crafted auto-mode questions 163–205 s (a two-iteration answer spent 169 s in workers). Tick 10: 20 of 24 probe searches timed out (18 HTTP 504 lines in the log). Tick 11: medians back to 2.7 s with the embedder at 40 % CPU (text-heavy literature needs fewer embeddings per minute), still 8 of 24 timeouts; the latency tracks the embedder's load, not the corpus size. Tick 12: medians 4.9 s hybrid / 1.2 s semantic, 5 of 24 timeouts, embedder back at 100 %. Tick 13: 18 of 24 timeouts, medians 30 s, chat 79–172 s, one hand-crafted auto answer 274 s (284 s in workers), the slowest of the run. Tick 14: 21 of 24 probe searches timed out (the most yet), scripted chat 45–264 s. Tick 15: **24 of 24 timed out**, the first complete blackout of the HTTP search path; chat (which embeds in-process without the 30 s limit) still answered in 154–156 s, but one hand-crafted auto-mode question hit the sessions endpoint's 300 s limit and returned HTTP 504, the first chat timeout of the run. Tick 16: second consecutive full blackout (24 of 24), scripted chat 127–139 s, and a second hand-crafted auto-mode question lost to the 300 s chat limit. Tick 17: 23 of 24 timeouts, scripted chat 169–223 s, hand-crafted auto answers 195 and 268 s. Tick 18: 24 of 24 timeouts, and the scripted title-summary chat itself hit the 300 s limit (HTTP 504), the first scripted chat lost; 3 of the 4 auto-mode chats of this tick exceeded 300 s, only fast mode still answers (146 s). Tick 19: 24 of 24 search timeouts; the two scripted chats finished at 297 and 298 s, one or two seconds under the limit; of the hand-crafted auto questions one timed out and one took 273 s. Tick 20: 24 of 24 search timeouts, and the control chat itself hit 300 s for the first time (HTTP 504). | 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F19 | low | **Doubled-letter text from overprinted PDFs.** Bold-by-overprint or shadow text is extracted as "B B E E Z Z I I R R K K S S G G E E R R I I C C H H T T" (`GASTEC-EG 1190/Attachements/20240618___VORBEREITENDER SCHRIFTSATZ.pdf`); 49 of 8,727 window documents and 191 of 52,585 overall match the pattern (Unternehmenskaufverträge, Grunderwerb forms, Rangordnungserklärungen). Fix: detect runs of doubled characters after text extraction and collapse them, or re-run such pages through OCR. Tick 10: two copies of `GASTEC-STEINBÖCK/Attachements/20241001_Vollmachtsbekanntgabe und Vertagungsbitte…` ("B BE EZ ZI IR RK KS SG GE ER RI IC CH HT T"). | 3, 10 |
| F20 | high | **Synthesis confuses fields and entities of same-type documents.** The Honorarnote question named the insured ("Ihr VN: Sila Schweißtechnik GmbH") as the addressee although the note is addressed to D.A.S. Rechtsschutz AG; in fast mode the KSV question added that "another document" shows rating 411, which is the KSV profile of Agrar-Brücke GmbH (`CZ-1855/898379409-101-7229299.pdf`), not Schilling Werbe; tick 2's case-number answer merged a cost ledger of another case the same way (F17). Fix: make the synthesis prompt state which entity each source is about and refuse to merge values from sources whose entity differs from the question's; show the case folder in citations (F15) so users can spot it. Tick 4 (raised to high): asked why the judgment 17 Cg 114/11a (`11_FeiBad_1/Sonstige Erledigung, Urteil, 2.pdf`) dismissed the claim, both agent modes answered "mangels passiver Klagslegitimation, § 52d Abs 6 ÄrzteG" and cited that title; the judgment contains neither phrase, the reasoning belongs to the same-titled judgment and pleadings of the unrelated case `Zürich_AnsaEv/`. Amounts and dates were right, the legal reason was invented from another case. Tick 6: fast mode reported "EUR 34.000,00 samt Anhang" (the Streitwert line of the case) as the amount paid to the expert. Tick 7 (minor): the fee-note answer listed the "außertourliche wesentliche Ermäßigung 3.000,00" (a discount) as "Ermächtigung" among the cost items, with the right figures. Tick 10: both modes said the *Vermieter* is bound to the Mietanbot until 31.03.2026, but the offer is the tenant's ("Ich stelle Ihnen … Mietanbot, an das ich mich … gebunden halte"), a party-role swap. Tick 17: asked for the payable amount of a contractor's Schlussrechnung (EUR 33.301,06 on the invoice), the agent reported EUR 29.193,34 "laut Schlussrechnung", which is the client's Rechnungsprüfbericht figure after a 3 % Haftrücklass; the figures are right, the document they belong to is not, and the discrepancy between invoice and audit went unmentioned. Tick 19: asked about the Rekursbeantwortung of 15.10.2018 in 5 MSch 13/15h (Rücklagenüberschuss, LG ZRS Wien 38 R 88/16x and 38 R 147/17z), the agent got the motion and the subject right, then continued with the arguments of a second "Rekursbeantwortung" in the same case folder (radiator maintenance, § 16 Abs 3 WEG) and never named the decisions asked for; fast mode mixed the Erstentwurf of a lease with its Konditionsblatt and a later version. Tick 20, the worst case so far: asked for the conclusion of the expert opinion GV_1/20 (which states "Photoepilation unterliegt nicht dem ÄrztInnen-Vorbehalt"), auto mode retrieved only its cover page and presented the opposite position from the firm's 2022 letter to the WKO ("dem Ärztevorbehalt unterfallend") as "the decisive legal classification" of the opinion. A reader would take away the reverse of what the Gutachten says. The Notariatsakt question was likewise padded with capital-increase clauses of two other companies. | 3, 4, 6, 7, 10, 17, 19, 20 |
| F21 | low | **Mislabelled file formats fall through to the raw-text fallback.** `P-1322/Buchhaltungsjournal 2003.pdf` is a Word `.doc` with a `.pdf` extension: Docling sniffed it as MS Word, its msword backend demanded LibreOffice (not in the image, traceback in the log), the document was declared "not valid" and the raw-text fallback stored it. Real `.doc` files go through antiword (`/usr/bin/antiword` is present in both images; 129,159 `.doc` files wait on the shares). Fix: when Docling reports a format other than the extension, route by the sniffed format (antiword for `.doc`), not by the name. Also 2 genuinely invalid PDFs today (`RechnungLutz.pdf`, an OGH Volltext). Tick 6: `Fink INSOLVENZRECHT.pdf` (3 copies) and `FP-POGRI-LEITZ-PRIV/webErv/Beilage B.pdf` rejected by docling-parse, stored through the fallback. Tick 7: a `Grundbuch_….pdf` whose bytes match no Docling input format ("format None"), also through the fallback. Tick 8: `Storch Krankengeschichte EKH.pdf` "not valid". Tick 11: six copies of `w_leuthner_2020-09-16-140140.pdf` "not valid", all stored through the fallback (see F26). Tick 12: three more copies of the same file. Tick 14: four more (13 copies so far). | 4, 6, 7, 8, 11, 12, 14 |
| F22 | low | **Absence claims are made from the retrieved chunks, not the document.** The Prafa policy question got the sums right but stated that the insurer "wird nicht explizit genannt", while the policy's last chunk is signed "Generali Versicherung AG" (and lists "Uniga [Uniqa] Österreich" as co-insurer). Fix: phrase absence as "in den abgerufenen Abschnitten", or fetch the remaining chunks of a document once it is identified as the answer's source. Tick 6: "kein spezifischer Streitwert genannt" for 33 Cg 31/26f although the order's "Wegen: EUR 87.000,00 samt Anhang" line sits in a small form-fragment chunk (F16) that was not retrieved. Tick 8: both modes said the GmbH guide does not define the function of the Stammkapital, although its own sentence "bildet einen Haftungsfonds für die Gläubiger" stands right next to the EUR 35.000 figure; auto mode "derived" functions from other sources instead. Tick 10: fast mode declared the Mietanbot's "mietfreie Zeit" absent while quoting the same document's rent figures from the contract summary instead. Tick 12: fast mode "keine Informationen über das Kautionsguthaben" although the demand letter's composition table lists "Kautionsguthaben inkl Zinsen EUR 6.024,33". Tick 13: both modes found the header chunk of a two-chunk Honorarnote (client, Causa, Bemessungsgrundlage) and declared its total absent; the second chunk is the five-line amounts table ("Honorar € 177,30 … Gesamtsumme € 220,30"), which neither the vector nor the text query reached. Small label/value tables are the systematic blind spot behind most absence claims (F16). Tick 14: both modes retrieved the "Dokumentinformation" header chunks of two editions of the WK StGB § 146 commentary (author, Stand) and declared the Strafdrohung absent, although the next chunk of each is the Normtext ("Freiheitsstrafe bis zu sechs Monaten oder Geldstrafe bis zu 360 Tagessätzen"). Fetching the neighbouring chunks of a hit document before synthesis would close most of these. Tick 15: same pattern on the GeKo Wohnrecht § 23 IO commentary (header retrieved, Normtext not); fast mode's wording "der eigentliche Inhalt ist nicht mitgeliefert" is the honest form this finding asks for, auto mode simply left the question unanswered. Tick 19: fast mode said the lease draft contains neither start, duration nor rent, while the Erstentwurf states 01.07.2017, 21 months and EUR 4.102 net; it had retrieved the object and deposit clauses only. Tick 20: for the 14-page Gutachten GV_1/20 both modes received only its cover-page chunk; fast mode said so ("lediglich die Überschrift"), auto mode filled the gap from another document (F20). | 4, 6, 8, 10, 12, 13, 14, 15, 19, 20 |
| F23 | high | **A redeploy during a running job throws away its position and its in-flight files, and the resume retries every file that had failed.** At 22:56 UTC the backend, worker and frontend were recreated from new images (`recallhub-backend:db-stats`, built from main `fd8211b`, rollback tags `rollback-20261025`). The API marked the job interrupted and resumed it, which means: discovery of the 401,135 files runs again (minutes of share scanning), the incremental filter reloads 63 k sources, all counters restart at 0, the 16 conversions that were in flight are dropped without a record (F2), and clients saw "server disconnected" for about six seconds. Fix: a graceful drain (stop taking new files, let in-flight conversions finish, persist the queue index) on SIGTERM, and a resume that reuses the persisted file list instead of rescanning. Tick 6 (raised to high): the incremental filter only knows the documents collection, so every file that had timed out or produced no chunks before the restart is "new" again and, being small, sits at the front of the queue: 553 timeouts on 451 distinct files and 697 no-chunk files in the first 4 h after the resume, all 16 timeout files sampled before the restart among them, throughput down to 18 files/min (733 documents/h versus 1,600–2,800 before). Persisted per-file outcomes (F2) plus a "skip known failures unless retry is requested" filter would avoid it. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: a resumed job skips the files its own `failed_documents` records name unless a `retry_*` flag asks for that error type (`failed_paths_of_job`, pipeline and API queue build)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 5, 6 |
| F24 | low | **No active Strategy OS spec for the Quellex tenant.** Every chat logs `no active spec for capability_id=legal_matter_qna tenant_id=quellex, falling back to legacy adapter`, so the config-driven runtime in `backend/agent/strategy/` is unused in production and the answers come from the legacy orchestrator. Either activate a spec from `backend/config/strategy_specs/` for the tenant or silence the warning. Tick 10: 5 more occurrences. Tick 13: 5 more. Tick 14: 5 more. Tick 15: 4 more. Tick 16: 4 more. Tick 17: 4 more. Tick 18: 4 more. Tick 19: 4 more. Tick 20: 4 more. | 6, 10, 13, 14, 15, 16, 17, 18, 19, 20 |
| F25 | high | **Entity questions lose their best document as copies of related documents accumulate.** The control question ("ALPO CONSULT GmbH, FN 404945h, Geschäftszweig") was answered from the Firmenbuchabfrage (`F-BlatLj_GeweR/Firmenbuchabfrage, FN 404945h, ALPO CONSULT GmbH.pdf`, hybrid rank 1) at ticks 1–5 and failed at ticks 6 and 7: the extract still exists unchanged, but hybrid and text search now fill the top 10 with Finanzamt letters and a Ratenansuchen that carry "ALPO CONSULT GmbH" in their address block, each present 2–6 times (F4), plus Firmenbuch boilerplate of other companies, and `hybrid_search` only fetches `2 × match_count` candidates per index before RRF. Fix: collapse copies (same `content_hash`) before fusion, fetch 5–10× candidates (`numCandidates ≥ 20 × limit`), and give exact identifier hits (FN, EZ/KG, Aktenzeichen) a dedicated keyword field with a boost (ties in with F17). Tick 8: the extract was back among the 10 sources (below rank 5) and the answer gave only "Unternehmensberatung" instead of the full "Unternehmensberatung, Buchhaltung und Handel", so the ranking hovers at the edge of the window. Tick 9: wrong again ("Geschäftszweig … nicht explizit aufgeführt"), the extract absent from the sources, Ratenansuchen and Finanzamt copies on top. Tick 10: wrong again, same pattern (4 failures in the last 5 ticks). Tick 11: wrong again (5 of 6); PR #28 carries the fix but is not deployed. Tick 12: wrong again (6 of 7), this time Honorarnoten of the client on top. Tick 13: correct for once (the extract among the 10 sources, below rank 5), so the document sits right at the edge of the candidate window: 2 passes in the last 8 ticks. Tick 14: correct again (3 of the last 9), 214 s. Tick 15: half right ("Unternehmensberatung" only, from Honorarnoten; the extract not in the top sources). Tick 16: wrong ("Geschäftszweig nicht genannt", a letter's addressee presented as the company's manager). Tick 17: wrong again, 223 s. Tick 18: half right ("Beratung (Consulting)", inferred from a letter's address line, not from the extract). Tick 19: half right again ("Unternehmensberatung" from a Honorarnote), 297 s. Tick 20: no verdict, the control request timed out (F18); a hand-crafted question naming "FN 319646v" did not retrieve the Notariatsakt that carries the number (F17). **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: identifier-aware retrieval with copies collapsed and wider candidate windows (PR #28)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 |
| F26 | medium | **Unreadable files are stored as documents whose text is the error message.** When Docling rejects a PDF and the raw-text fallback cannot read it either, the pipeline stores `[Error: Could not read file <name>]` as the document content, chunks it (1 chunk) and embeds it. 19 such documents exist (`P-106-10/Grundbuch_31.08.2012.pdf`, `P-1322/Buchhaltungsjournal 2003.pdf`, `…/RechnungLutz.pdf`, `KCZ-78/Geb1.pdf`, a Geschäftsverteilung of the HG Wien, six copies of `w_leuthner_2020-09-16-140140.pdf`, …). They count as ingested, so they are never retried, and a question that names the file gets an error string as its "source". Fix: treat a failed fallback as a failure (`error`/`invalid_format` in `failed_documents`, F2) instead of a document; backfill by deleting documents whose content starts with `[Error:`. Tick 15: one more (`EKH_DlasWo/Unterlagen/w_leuthner_2019-12-23-120011.pdf`), a second file of the same unreadable kind. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: reader placeholders raise `UnreadableFileError` and are recorded as failed; `quellexctl ingest purge-placeholders --profile diejuristin --apply` removes the 19 existing documents and their chunks); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 11, 15 |
| F27 | low | **The llama.cpp embedder returned HTTP 500 nine times within 8 minutes (21:57–22:05 UTC) with nothing in its own log.** The ingestion embedder client retried ("OpenAI server error (attempt 1/3), retrying in 1.0s") and no chunk in the window lacks an embedding (37,912 checked), so nothing was lost, but only 2 of the 9 errors show a retry line: the others hit the query-embedding path of search/chat (`backend/services/query_embedding.py`), which does not retry, and surfaced as failed searches. Fix: retry with backoff on the query path too, and count embedder 5xx in the host-stats widget. | 13 |
| F28 | medium | **Long questions silently break the text stage of hybrid search.** Two chat requests on 26.10. 09:07 and 09:15 UTC (the loop's 40-word hand-crafted questions) logged `Text search failed for rag_diejuristin: Query contains too many nested clauses; maxClauseCount is set to 1024`: the `$search` text operator with `fuzzy: {maxEdits: 2, prefixLength: 3}` (`backend/agent/federated_search.py:312`, same in `backend/routers/search.py`) expands every term into dozens of variants, so a question with 25+ distinct terms exceeds Lucene's clause limit; the exception is caught as a warning and the answer is built from the vector stage alone, which is exactly when exact figures go missing. Fix: fuzzy only on terms of 5+ characters with `maxExpansions` capped, or extract the top-N keywords for the text stage, or retry the text stage without fuzzy on this error. | 17 |
| F29 | medium | **The single GPU embedder is now the ceiling for ingestion and the shared bottleneck for search.** At tick 19 three `docker stats` samples showed the backend at 0.4–0.7 % CPU and the embedder at 100 %; in ten minutes 56 files finished converting and 56 "Generating embeddings" waits produced 62 embedding batches, i.e. with the text-heavy literature now in the queue every file converts instantly and then waits for its embeddings. Ingestion throughput therefore follows the embedder's batch rate (and has fallen from 31 to 13 files/min as documents grew longer), while every query embedding joins the same queue (F18). Fix: run a second embedder instance (the `embedder` CPU profile already exists; the ingestion pipeline could use it or a GPU instance with its own slots), or give query embeddings a reserved slot, and batch ingestion chunks per file instead of per 100. **Fixed in main `a583c6e`** (2026-10-26, PR #29, commit `6b78868`: query embeddings retry with backoff and can use a second embedder (`QUERY_EMBEDDING_URL`, compose profile `embedder-query`, port 11091); the live embedder computes on the GPU (13-16k tok/s after a restart vs 606 tok/s degraded, see docs/04)); not deployed yet: rebuild `recallhub-backend` and the worker image from `main` with a side tag and start them from `C:\RecallHub-Verify`. | 19 |

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
