# Why pure semantic search on the synthetic corpus dropped between bench1 and bench6 (2026-10-23)

Follow-up to `GEMMA4_12B_BUILD_COMPARISON_2026-10-22.md`, section "Retrieval". That report attributed the semantic drop
(Hit@1 37 % → 20 %, MRR 0.50 → 0.39 on the 41 answerable gold questions of `C:\RecallHub_Synthetic\kanzlei_v1`) to the
re-ingestion. This note supersedes that attribution.

## Verdict

**Cause: the embedding model changed, not the ingestion.** bench1 (2026-10-11) was embedded and queried with OpenAI
`text-embedding-3-large` (3072-d). Since the 2026-10-14 local-only reset the corpus and every query use the local
EmbeddingGemma 2 Q8_0 (llama.cpp, 768-d) without the model's task prefixes. The two models rank this German legal corpus
differently, and EmbeddingGemma 2 separates the documents of one case less sharply. Everything else was tested and is clean:
the stored chunk vectors match fresh embeddings of the same text (cosine ≥ 0.9998 on all 774 chunks), `$vectorSearch` with
`numCandidates: 100` returns the exact top 10 (99.8 % overlap, identical metrics at 1000), the index is 768-d cosine
and covers every chunk, and the re-ingested chunk text is the same or better (109 of 124 files chunk identically, scan
fact retention 44 % → 95 %).

**Effect on the hybrid search the agent uses: none that matters.** Hybrid is level with bench1 or better (Hit@1 46 → 49 %,
MRR 0.58 → 0.63) because the text leg improved with Tesseract OCR and the text leg dominates the top of the RRF ranking on
this corpus in both runs. On the bench6 data, text search alone scores Hit@1 54–56 %, hybrid 39–49 %, semantic 17–20 %:
the semantic leg adds a little recall at rank 10 (90 → 93–95 %) and takes a little off the top-1.

**Fix (implemented, see "Verification of the fix" below):** the EmbeddingGemma query prefix
(`task: search result | query: `) is now applied on the query side by `backend/services/query_embedding.py`, shared by
the search router, the chat router and the federated search, gated on the model family (`EMBEDDING_QUERY_PREFIX`
overrides or disables it). No re-embedding is needed. Measured through the search API (bench7_qprefix vs bench6):
semantic Hit@1 20 → 27 %, Hit@10 80 → 85 %, MRR 0.39 → 0.44, passage hit@10 54 → 69 %; hybrid Hit@5 83 → 88 %,
Hit@10 93 → 95 %, recall@10 68 → 71 %, while hybrid Hit@1 (49 → 44 %) and MRR (0.63 → 0.61) moved inside the
tie-order noise band described below. Document prefixes (`title: … | text: …`) gave nothing on this corpus and would
need a full re-embed; not done. Recovering bench1's semantic numbers would mean a different embedding model, which is a
decision, not a bug fix.

## Evidence for the cause

| Candidate | Test | Result |
|---|---|---|
| Embedding model at bench1 | `bench_ingest` creates `vector_index` with its `dimensions` default 3072 and the CLI never overrides it; bench1's `ingest.json` records `search_indexes.created = [vector_index, text_index]`. Atlas does not index vectors whose length differs from `numDimensions`, and bench1 semantic had Hit@10 85 %, so its vectors were 3072-d. `LOCAL_VS_NETWORK_MODE_VALIDATION_2026-10-11.md` records the live `.env` of that day: `text-embedding-3-large`, 3072 dims, "embeddings stay on OpenAI". | bench1 = OpenAI 3-large |
| Embedding model at bench6 | `vector_index` of `rag_synth_kanzlei_v1.chunks` is 768-d cosine, created 2026-10-14 23:17 (ObjectId timestamp; the local-only reset, see the `local-stack-state` memory: "both vector indexes recreated at 768 dimensions, synthetic corpus re-ingested with local embeddings, job 2026-10-14 23:18"). All 774 chunks carry `embedding_model: embeddinggemma-2`, embedded 2026-10-15 22:38 UTC (the Tesseract re-ingestion, `bench5_tesseract`). The `rag-embedder-gpu` container (llama.cpp `server-cuda`, `ggml-org/embeddinggemma-2-GGUF:Q8_0`, 4 slots × 8192 ctx) dates from 2026-10-14 23:16 and served both ingestion and the 2026-10-22 queries. | bench6 = local EmbeddingGemma 2 |
| Corroboration | bench1 semantic latency p50 315 ms vs text 14 ms (an API round trip); bench6 65 ms vs 20 ms. bench1's ingest sampled no embedder container and 888 MB GPU memory max (the GPU embedder holds 3.7 GB). `EMBEDDINGGEMMA2_EVALUATION_2026-10-10.md` had measured only 60 % top-10 neighbour overlap between the two models and asked for a labelled eval before cut-over; this is that eval. | consistent |
| Stored vectors vs fresh embeddings | All 774 `content` strings re-embedded through `127.0.0.1:11090/v1/embeddings` (raw text, as `src/ingestion/embedder.py` sends it: no prefix, `_sanitize_text` only) and compared with the stored vectors. | cosine min 0.99979, median 0.99991; none below 0.999 |
| Query vs document embedding path | `backend/routers/search.py:get_embedding` sends the raw query through the OpenAI client to the same server; both sides unit-normalised (stored norms 1.000000, server returns unit vectors). No prefix on either side. | symmetric, no mismatch |
| Vector index and `numCandidates` | Exact cosine over all chunks vs `$vectorSearch` (`numCandidates` 100 and 1000, limit 10) for the 41 queries. | top-10 overlap 99.8 %, metrics identical; index covers all chunks |
| Re-ingestion (chunk boundaries, OCR) | Per-file chunk counts of bench1's `ingest.json` vs the current collection: 109 of 124 files identical; 6 scans gained one chunk each (Tesseract reads more), 8 PDFs and 1 DOCX changed by ±1–5 chunks (Docling/table changes); tokens 214 k → 222 k. Chunk text of digital documents carries no new boilerplate (no page furniture, `INGEST_PAGE_FURNITURE` off). Scan fact retention 44 % → 95 %; text search improved (Hit@1 51 → 56 %, Hit@10 80 → 90 %). | text got better, cannot explain a semantic loss |
| Current semantic quality reproduces bench6 | Re-running semantic search today on the stored vectors: Hit@1 15–17 %, Hit@10 80 %, MRR 0.36 (bench6 file 20 % / 80 % / 0.39; the ±1-rank differences on 9 questions are tie order among duplicate copies). | the drop is stable, not a bad run |

## Question by question (semantic, first relevant rank, bench1 → bench6)

19 questions worse, 11 better, 11 unchanged. Six lost the gold document from the top 10 and four gained one. In every lost
case the replacements are other documents of the same case file (same parties, same case number), i.e. the ranking inside
a case became flatter:

| Question | Gold document(s) | bench1 → bench6 | What is in the top 5 now |
|---|---|---|---|
| arbeitsrecht_01-q04 (single_fact) | 2025-07-08 Beweisaufnahme … Ing. Hanna Zenz.pdf | 4 → none | Einspruch samt Klagebeantwortung ×2, Dienstvertrag-Nachtrag scan ×2, Mahnklage |
| bau_01-q05 (scan_ocr) | 2024-09-16 Angebot und Werkvertrag … Seite 1.jpg | 3 → none | Übernahmeprotokoll Seite 2.jpg, Begründeter Einspruch, Mahnklage, Verbesserungsablehnung, Mandatierung.eml |
| datenschutz_01-q01 (exact_token) | 2025-10-10 Teilweise stattgebendes Urteil.pdf, 2025-11-17 Bestätigung der Zahlung.eml | 1 → none | DSB-Bescheid, Zahlungsbefehl Seite 1.jpg, DSB-Aufforderung, Schadenersatzforderung, Honorarnote |
| inkasso_01-q01 (exact_token) | 2025-05-02 Erste Drittschuldnerzahlung.pdf, Zwischenabrechnung, Mitteilung | 6 → none | Letzte eigene Mahnung, Zahlungsbefehl, Rechtskraftbestätigung ×2, Betreibungsauftrag |
| mietrecht_01-q05 (scan_ocr) | 2024-09-02 Hauptmietvertrag … Seite 2.jpg | 3 → none | Hauptmietvertrag Seite 1.jpg, Betriebskostenabrechnung, Vertragsprüfung.docx, Mandatsauftrag ×2 |
| cross-q02 (cross_case) | four Erstberatung/Vertragsprüfung .docx across cases | 2 → none | Beschwerde Fitz, Mandatsauftrag Hammerl, Ablehnung Eberhard ×2, Schriftliche Geltendmachung |
| gained | arbeitsrecht_01-q05, bau_01-q02, bau_01-q07, datenschutz_01-q07 | none → 6, 2, 2, 2 | |

The two scan questions that dropped (bau_01-q05, mietrecht_01-q05) dropped although their OCR text is far better now:
the gold scan pages now compete with their own digital neighbours in the same case, which 3-large kept apart.

## Prefix experiment (exact cosine, in memory, no database writes)

EmbeddingGemma 2's model card asks for `task: search result | query: ` on queries and `title: … | text: ` on documents;
production sends neither. All 774 chunks were re-embedded in memory in three document variants and the 41 questions in
three query variants:

| Query | Documents | Hit@1 | Hit@5 | Hit@10 | MRR | Recall@10 |
|---|---|---|---|---|---|---|
| raw (production) | stored | 15 % | 61 % | 80 % | 0.351 | 52 % |
| raw | fresh raw | 17 % | 63 % | 80 % | 0.362 | 52 % |
| **`task: search result \| query:`** | **stored** | **27 %** | **66 %** | **85 %** | **0.428** | **58 %** |
| `task: search result \| query:` | `title: none \| text:` | 15 % | 68 % | 83 % | 0.346 | 57 % |
| `task: search result \| query:` | `title: <doc title> \| text:` | 17 % | 66 % | 83 % | 0.379 | 54 % |
| `task: question answering \| query:` | `title: none \| text:` | 24 % | 63 % | 83 % | 0.391 | 55 % |
| raw | `title: none \| text:` | 17 % | 68 % | 85 % | 0.375 | 58 % |
| bench1 file (3-large) | | 37 % | 71 % | 85 % | 0.501 | 66 % |

The query prefix alone is the only variant that helps, and it works against the vectors already stored. Prefixing the
documents as well does not help here and would cost a full re-embed of every tenant.

## Hybrid search replicated offline

`backend/routers/search.py:hybrid_search` replicated with the real `$search` text leg (fuzzy, limit 20), an exact-cosine
vector leg (limit 20), RRF k = 60, top 10:

| Variant | Hit@1 | Hit@5 | Hit@10 | MRR | Recall@10 |
|---|---|---|---|---|---|
| text leg only | 54 % | 83 % | 90 % | 0.640 | 67 % |
| semantic leg only (stored vectors) | 17 % | 61 % | 80 % | 0.363 | 52 % |
| hybrid as coded (stored vectors) | 39 % | 83 % | 93 % | 0.577 | 68 % |
| hybrid, text leg first on RRF ties | 41 % | 83 % | 93 % | 0.586 | 69 % |
| hybrid, query prefix (no re-embed) | 44 % | 88 % | 95 % | 0.609 | 71 % |
| bench6 file (hybrid, API, 2026-10-22) | 49 % | 83 % | 93 % | 0.634 | 68 % |
| bench1 file (hybrid, API, 2026-10-11) | 46 % | 71 % | 95 % | 0.582 | 68 % |

The replication matches the bench6 file on 36 of 41 questions; the other five are ties between byte-identical duplicate
chunks (the `_Ablage`/`04_Beweismittel` copies score exactly the same, and which copy comes first is arbitrary), which is
why hybrid Hit@1 moves by up to four questions between otherwise identical runs. Read Hit@5/MRR, not Hit@1, when comparing
hybrid runs on this corpus.

## Verification of the fix (bench7_qprefix, 2026-10-23)

The query prefix was implemented in `backend/services/query_embedding.py` (`detect_task_prefixes` moved there from the
validation checks and re-exported; `backend/routers/search.py`, `backend/routers/chat.py` and
`backend/agent/federated_search.py` call `embed_query`; new setting `EMBEDDING_QUERY_PREFIX`; tests in
`backend/tests/test_query_embedding.py`, 19 cases). `bench-retrieval` then ran against a backend started from the
checkout on `127.0.0.1:11002` with the live container's environment mirrored exactly as in the 2026-10-22 report (same
database, same stored vectors, same embedder, `REQUIRE_AUTH=false`, `TELEMETRY_HISTORY_ENABLED=false`); the backend log
confirms `Query embedding prefix for model 'embeddinggemma-2': 'task: search result | query: '`. 123 requests, 0 errors.
Raw: `C:\RecallHub_Synthetic\kanzlei_v1\bench\bench7_qprefix\retrieval.json`.

| Mode | Run | Hit@1 | Hit@5 | Hit@10 | Recall@10 | MRR | Passage hit@10 | Passage recall@10 | p50 ms |
|---|---|---|---|---|---|---|---|---|---|
| semantic | bench6 (no prefix) | 20 % | 68 % | 80 % | 52 % | 0.385 | 54 % | 44 % | 64.6 |
| semantic | **bench7 (query prefix)** | **27 %** | 68 % | **85 %** | **58 %** | **0.441** | **69 %** | **59 %** | 64.5 |
| hybrid | bench6 (no prefix) | 49 % | 83 % | 93 % | 68 % | 0.634 | 77 % | 67 % | 74.2 |
| hybrid | **bench7 (query prefix)** | 44 % | **88 %** | **95 %** | **71 %** | 0.609 | **80 %** | **69 %** | 70.8 |
| text | both | 56 % | 83 % | 90 % | 67 % | 0.653 | 77 % | 69 % | 15–20 |

Per question (first relevant rank): semantic 15 better, 8 worse, 18 unchanged; hybrid 7 better, 10 worse, 24 unchanged;
text identical, as it must be. The semantic gain matches the offline prediction (27 % / 0.428). For hybrid the top-1
loss of two questions and the MRR change sit inside the ±4-question tie-order band (the offline replication of the very
same bench6 vectors scored 39 % / 0.577), while the depth metrics the agent relies on (Hit@5, Hit@10, recall@10, passage
hit@10) all improved. Latency is unchanged.

## What to do

1. **Nothing for the live stack beyond deploying the prefix change.** The agent's hybrid search is at or above bench1
   level; the semantic leg was weak at the top in both runs and the text leg carries the ranking. The 2026-10-22 report's
   sentence "differences to bench1 come from ingestion" should be read as "come from the embedding model switch of
   2026-10-14".
2. **Query prefix: done** (section above). It ships with the next backend image; no re-ingestion, no index change.
   Documents stay unprefixed.
3. **Not warranted now:** re-embedding with document prefixes (no gain), raising `numCandidates` (ANN already exact on
   774 chunks; on the 100 k-chunk tenants keep the 20 × limit rule from `CLAUDE.md`), changing RRF tie order (+1 question).
4. **If semantic quality itself matters later** (e.g. for paraphrased questions where the text leg fails), run a local
   embedding-model A/B on this gold set the same way (re-embed in memory, exact cosine), because EmbeddingGemma 2 Q8_0
   without prefixes is measurably weaker than 3-large on this corpus (Hit@1 37 vs 15–17 %, MRR 0.50 vs 0.35).

## Method and raw results

- Read-only: no ingestion, no index change, no writes to any database; `rag_diejuristin` was not read (its chunk
  collection is empty anyway).
- Scripts and JSON outputs: `C:\RecallHub_Synthetic\kanzlei_v1\bench\semantic_regression_2026-10-23\`
  (`query_side.py` stored-vs-fresh sample, ANN vs exact, query prefixes; `reembed_all.py` full in-memory re-embed and the
  prefix matrix; `hybrid_offline.py`, `hybrid_ann_check.py`, `hybrid_tiebreak.py` the hybrid replication; `*.json` results;
  `docs_*.npy` the in-memory document vectors). Run with the repo `.venv` python, argument = output folder; they need the
  stack's MongoDB on 127.0.0.1:11017 and the embedder on 127.0.0.1:11090.
- Inputs compared: `bench/bench1/retrieval.json` + `ingest.json` (2026-10-11), `bench/bench6_12b/retrieval.json`
  (2026-10-22), `bench/bench5_tesseract/ingest.json` (the 2026-10-15/16 re-ingestion that produced the current chunks),
  `gold/questions.json` (unchanged since 2026-10-11 10:30).
- Metrics follow `score_ranking` in `backend/evaluation/synthetic_corpus/bench.py` (chunk list with duplicates, first
  relevant rank by exact `document_source` match against `gold_documents`).
