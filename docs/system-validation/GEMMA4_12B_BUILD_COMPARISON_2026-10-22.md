# Gemma 4 12B: new Ollama build (imatrix Q4_K_M + MTP drafter) vs June build — agent and retrieval evaluation (2026-10-22)

Machine: DESKTOP-8ESH62J (Core Ultra 9 285HX, RTX PRO 2000 Blackwell 16 GB at its 70 W power cap, driver 597.16),
Windows 11, Ollama 0.40.6 (native app), `rag-embedder-gpu` (llama.cpp, EmbeddingGemma 2 Q8_0) resident on the same GPU.
Backend code: `main` at `387b35a` (the commit the live `recallhub-backend:latest` image was built from on 2026-10-19).

## Why

On 2026-10-19 the production tag `gemma4:12b` was re-pulled. Ollama had re-published it (around 2026-10-04) as a new
imatrix-calibrated Q4_K_M quantization with a separate projector and Google's MTP drafter layer. The speed benchmark
(`GEMMA4_12B_MTP_BENCHMARK_2026-10-19.md`) showed decode going from 30 to 51 tok/s with unchanged prefill, but the
weights are a different quantization, so answer quality had not been re-validated. This run re-establishes the
agent/retrieval baseline on the production tag and compares it with the previous build, question by question.

## Models

| Tag | Weights | Drafter | Role in this run |
|---|---|---|---|
| `gemma4:12b` | new build, Q4_K_M imatrix, blob `bb722270…` (parent `imatrix-20260927-12b`) | `7008a656…` (`draft_num_predict 3`) | arm **new**, production tag since 2026-10-19 |
| `gemma4:12b-june` | June 2026 build, Q4_K_M, blob `1278394b…` | none | arm **june**, `ollama cp` of the tag production ran until 2026-10-19 |

Both report the `thinking` capability under Ollama 0.40.6; the backend switches thinking off for every agent call
(`agent_ollama_think_phases` default, `reasoning_effort none` in `backend/agent/ollama_options.py`). Verified in every
trace of this run: `think: False`, `num_ctx: 32768` (the backend default), orchestrator and worker model equal to the arm's tag.

## Method

- Harness: the synthetic-corpus benchmark that produced the earlier reports (`python -m backend.evaluation.synthetic_corpus
  bench-retrieval / bench-chat / bench-ab / bench-report`, `docs/20-SYNTHETIC_CORPUS.md`). It drives the real chat path
  (`POST /api/v1/sessions/{id}/messages/stream`, the endpoint the UI uses) and scores against the gold set. The
  `quellexctl evaluation run` runner in `backend/evaluation/runner.py` was not used: it runs without a database and
  scores heuristically; it is not what bench1–bench4 used.
- Corpus and gold set: `C:\RecallHub_Synthetic\kanzlei_v1` (5 cases, 110 documents, 141 files; 45 gold questions:
  41 answerable, 4 unanswerable), profile `synthetic-kanzlei`, database `rag_synth_kanzlei_v1` as re-ingested on
  2026-10-14 with Tesseract OCR (141 documents, 774 chunks, both search indexes READY). The `rag_diejuristin` tenant data
  was not touched.
- Stack under test: the live `rag-backend-quellex` container was left as it is (login enforcement, `diejuristin`
  profile). Instead a second backend process ran from this checkout on `127.0.0.1:11002` with the live container's
  non-secret environment mirrored (`docker inspect`), differing only in: `MONGODB_DATABASE=rag_synth_kanzlei_v1`,
  `ACTIVE_PROFILE=synthetic-kanzlei`, `REQUIRE_AUTH=false`, `TELEMETRY_HISTORY_ENABLED=false` (keeps benchmark chats
  out of the shared `recallhub_telemetry` database), Ollama and the embedder reached through `127.0.0.1` instead of
  `host.docker.internal` / `embedder`, and `ORCHESTRATOR_MODEL` = `WORKER_MODEL` = `LLM_MODEL` = `FAST_LLM_MODEL` =
  the arm's tag (the live stack sets all four to `gemma4:12b`). Everything else (agent options, match counts,
  disabled tools, tenant id `quellex`) matched the container. The embedding model and search indexes are shared by both arms.
- Gotcha for reruns: `load_llm_config_from_db()` (`backend/routers/system.py`) copies `llm_config/provider_config`
  (orchestrator and worker model) over the environment at startup. The synthetic database stores `gemma4:12b` there, so
  the June arm needed that document pinned to `gemma4:12b-june` for its run; it was backed up and restored to
  `gemma4:12b` afterwards. The backend log line `Model routing: … source=global_config` and the `models` field of every
  trace confirm which tag each arm used.
- Order: one smoke question (new build), then retrieval, 45 thinking and 45 fast questions on the new build
  (14:58–15:41 UTC), then a backend restart with the June tag and the same 45 + 45 questions (15:42–16:42 UTC),
  concurrency 1. Each question gets a fresh session, deleted afterwards. Decoding is sampled (temperature 0.2 in the
  JSON phases, 0.7 for the answer), so single runs carry run-to-run noise; the paired A/B below is the right reading.
- Raw results: `C:\RecallHub_Synthetic\kanzlei_v1\bench\bench6_12b` (new) and `bench6_12b_june` (june): `chat_<mode>.jsonl`
  with every answer, trace and score, `chat_<mode>.summary.json`, `REPORT.md` (bench-report format of the earlier benches),
  `telemetry_chat_<mode>.html`, `retrieval.json`, and `ab_bench6_12b_june_vs_bench6_12b.json` (paired A/B) in `bench6_12b`.

## Runs that failed

None. 4 chat runs × 45 questions = 180 questions: 0 transport errors, 0 failed questions, 0 JSON retries, 0 outputs cut
at `max_tokens`, 0 repetition-guard truncations, 0 degradation fallbacks in the traces; retrieval 123 search requests, 0
errors. The only anomalies the harness flagged are listed under "Robustness" below; none is a failed run.

## Chat, mode `thinking` (the production agent path)

`bench4` is the last run of the live image with the old weights (2026-10-15, older code revision) and is shown as a
reference for run-to-run noise of identical weights.

| Measure | `bench4` (old weights, 2026-10-15) | `gemma4:12b-june` | `gemma4:12b` (new) |
|---|---|---|---|
| Questions / errors / failed runs | 45 / 0 / 0 | 45 / 0 / 0 | 45 / 0 / 0 |
| **Correct answers** (all required values, or ≥ 60 % of the nuggets) | 63 % (26/41) | 63 % (26/41) | 66 % (27/41) |
| All required values in the answer | 54 % | 54 % | 54 % |
| Required values found (share) | 71 % | 71 % | 71 % |
| Nugget recall | 74 % | 71 % | 74 % |
| Answers containing a draft (wrong) value | 0 | 0 | 0 |
| Answers cut off by the repetition guard | 0 | 0 | 0 |
| False abstentions on answerable questions | 0 % | 0 % | 0 % |
| Unanswerable questions correctly declined | 100 % | 100 % | 100 % |
| Agent sources: gold document in top 1 / top 10 | 27 % / 80 % | 37 % / 85 % | 37 % / 80 % |
| Agent sources: gold document recall@10 | 55 % | 56 % | 55 % |
| Agent retrieved the gold passage | 59 % | 59 % | 62 % |
| Citations per answer | 5.49 | 5.73 | 6.51 |
| Answer length (chars, mean) | 2150 | 2216 | 2057 |
| Tool calls per question | 3.27 | 3.38 | 3.31 |
| Queries with boolean syntax or quotes | 98 % | 100 % | 98 % |
| Queries empty / malformed (of all) | 0 / 0 (of 147) | 0 / 0 (of 152) | 0 / 0 (of 149) |
| Questions without any usable query | 0 % | 0 % | 0 % |
| Questions with a name spelled differently in a query | 4 % | 2 % | 2 % |
| **Latency per answer: median (p50) s** | 66.5 | 70.2 | **43.7** |
| Latency per answer: mean / p95 / max s | 68.2 / 82.9 / 93.8 | 72.0 / 86.0 / 120.7 | 45.2 / 57.4 / 60.1 |
| Phase medians: analyze / plan / synthesize s | 16.8 / 17.0 / 31.2 | 18.2 / 18.1 / 32.8 | 10.7 / 10.7 / 22.2 |
| Refinement iterations (evaluate phase ran) | 0 questions | 1 question (`inkasso_01-q06`, 120.7 s) | 0 questions |
| Ollama decode tok/s (whole run) | 28.5 | 30.3 | 59.2 |
| Ollama prefill tok/s (whole run) | 1197 | 1242 | 1196 |
| Largest prompt / context used / `num_ctx` | 14159 / 14932 / 32768 | 15546 / 16183 / 32768 | 14034 / 14776 / 32768 |

### Paired A/B, june (A) vs new (B), 45 questions

- Gained in B: `mietrecht_01-q03` (single_fact). Lost in B: none. Exact two-sided sign test p = 1.0.
- Restricted to the 34 questions with the same retrieval outcome in both arms: 0 gained, 0 lost. The one gain came from
  retrieval (the June run did not retrieve the gold passage, the new run did), not from answer writing.
- Latency: the new build was faster on **45 of 45** questions; per-question time ratio new/june median 0.62
  (range 0.43–0.82). Excluding June's one two-iteration question, June's median is 69.8 s, so the gap is not an artefact
  of that outlier.
- Hidden reasoning tokens per answer (estimate): 51 (june) vs 46 (new); budget hits 0 / 0; decode tokens per question 1650 vs 1594.

| Type | n | june correct | new correct | june p50 s | new p50 s |
|---|---|---|---|---|---|
| cross_case | 3 | 33 % | 33 % | 68.0 | 44.2 |
| exact_token | 10 | 70 % | 70 % | 65.6 | 41.3 |
| multi_doc | 10 | 70 % | 70 % | 79.7 | 46.2 |
| scan_ocr | 3 | 67 % | 67 % | 64.5 | 43.2 |
| single_fact | 10 | 70 % | 80 % | 67.9 | 43.3 |
| timeline | 5 | 40 % | 40 % | 75.7 | 45.8 |
| unanswerable (declined) | 4 | 100 % | 100 % | 70.2 | 43.2 |

### Where answers were lost (first failing stage, in pipeline order)

| Stage | june | new |
|---|---|---|
| Answer correct | 26 (58 %) | 27 (60 %) |
| Retrieval: gold document found, gold passage not in the retrieved chunks | 8 (18 %) | 6 (13 %) |
| Retrieval: gold document not among the agent's sources | 2 (4 %) | 4 (9 %) |
| Synthesis: evidence retrieved, answer misses values or nuggets | 5 (11 %) | 4 (9 %) |
| Unanswerable question correctly declined | 4 (9 %) | 4 (9 %) |

The wrong answers are the same 14 questions in both arms, plus `mietrecht_01-q03` in the June arm only:
`arbeitsrecht_01-q02`, `-q03`, `-q06`, `bau_01-q02`, `-q07`, `-q08`, `datenschutz_01-q01`, `-q06`, `-q07`,
`mietrecht_01-q04`, `-q05`, `-q08`, `cross-q02`, `cross-q03`. The failing stage moved between retrieval and synthesis
for five of them (sampled decoding), but no question flipped from wrong to right or back except the one gain. The two
`REPORT.md` files list the missing values per question.

## Chat, mode `fast` (single search, the model writes the answer directly)

| Measure | `gemma4:12b-june` | `gemma4:12b` (new) |
|---|---|---|
| Questions / errors / failed runs | 45 / 0 / 0 | 45 / 0 / 0 |
| **Correct answers** | 51 % (21/41) | 49 % (20/41) |
| All required values in the answer | 38 % | 38 % |
| Required values found (share) | 56 % | 56 % |
| Nugget recall | 57 % | 57 % |
| Answers containing a draft (wrong) value | 0 | 0 |
| False abstentions on answerable questions | 10 % | 10 % |
| Unanswerable questions correctly declined | 100 % | 100 % |
| Agent sources: gold document in top 1 / top 10 | 49 % / 93 % | 49 % / 93 % |
| Agent sources: gold document recall@10 | 66 % | 66 % |
| Agent retrieved the gold passage | 74 % | 74 % |
| Citations per answer | 1.62 | 1.89 |
| Answer length (chars, mean) | 471 | 515 |
| Tool calls per question | 1.0 | 1.0 |
| **Latency per answer: median (p50) s** | 7.8 | **4.7** |
| Latency per answer: mean / p95 / max s | 7.9 / 11.8 / 14.5 | 4.9 / 8.2 / 8.4 |
| Ollama decode tok/s (whole run) | 30.3 | 61.2 |
| Ollama prefill tok/s (whole run) | 1209 | 1132 |

Paired A/B: gained in new none, lost in new `inkasso_01-q05` (scan_ocr; identical evidence retrieved in both arms, the
new build's answer left out "Es besteht eine Unterhaltspflicht."), sign test p = 1.0. Retrieval is identical in fast
mode (the question is the only query), so every difference here is answer writing. The new build was faster on 41 of 45
questions, median time ratio 0.63.

| Type | n | june correct | new correct |
|---|---|---|---|
| cross_case | 3 | 33 % | 33 % |
| exact_token | 10 | 60 % | 60 % |
| multi_doc | 10 | 50 % | 50 % |
| scan_ocr | 3 | 67 % | 33 % |
| single_fact | 10 | 60 % | 60 % |
| timeline | 5 | 20 % | 20 % |
| unanswerable (declined) | 4 | 100 % | 100 % |

## Retrieval (search API, no LLM) — current baseline

Model-independent (EmbeddingGemma 2 + text index + manual RRF); re-run once as the baseline for both arms. The bench1
rows (2026-10-11) are not a like-for-like reference: bench1 was embedded and queried with OpenAI `text-embedding-3-large`
(3072-d), everything since the 2026-10-14 local-only reset uses local EmbeddingGemma 2 (768-d), and the corpus was
re-ingested on 2026-10-14 with Tesseract OCR (774 chunks instead of 777). Nothing here depends on the LLM under test.
The requests were paced to the backend rate limit; 0 errors.

| Mode | n | Hit@1 | Hit@5 | Hit@10 | Recall@10 | MRR | Passage hit@10 | Passage recall@10 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| hybrid (2026-10-22) | 41 | 49 % | 83 % | 93 % | 68 % | 0.63 | 77 % | 67 % | 74 | 94 |
| semantic (2026-10-22) | 41 | 20 % | 68 % | 80 % | 52 % | 0.39 | 54 % | 44 % | 65 | 73 |
| text (2026-10-22) | 41 | 56 % | 83 % | 90 % | 67 % | 0.65 | 77 % | 69 % | 20 | 25 |
| hybrid (bench1, 2026-10-11) | 41 | 46 % | 71 % | 95 % | 68 % | 0.58 | 77 % | 69 % | 364 | 474 |
| semantic (bench1) | 41 | 37 % | 71 % | 85 % | 66 % | 0.50 | 72 % | 64 % | 315 | 389 |
| text (bench1) | 41 | 51 % | 68 % | 80 % | 53 % | 0.59 | 72 % | 64 % | 14 | 18 |

Hybrid search, which the agent uses, is unchanged to slightly better at the top of the ranking. Pure semantic search
is lower than bench1 (Hit@1 37 % → 20 %, MRR 0.50 → 0.39) while text search gained. The follow-up
`SEMANTIC_SEARCH_REGRESSION_2026-10-23.md` traced the semantic difference to the embedding-model switch (OpenAI 3-large →
EmbeddingGemma 2), not to the re-ingestion: stored vectors, index and ANN all check out, hybrid is unaffected, and the
EmbeddingGemma query prefix is the one cheap improvement it measured.

## Robustness and resources

- Both arms ran with the GPU at its 70 W power cap and 95 °C maximum, 0 s of thermal slowdown. The June thinking run
  spent 3001 s at the power cap (of 3256 s), the new build 1713 s (of 2048 s): the drafter does the same work in less
  time and the GPU stays as hot. VRAM peak 13.4 GB in both thinking runs (12b model + 3.7 GB embedder + the 465 MB drafter on the new build).
- Ollama loaded each model once at 32768 context (6.5 s june, 8.5 s new incl. drafter); loading the June model evicted
  the new one (expected: only one 12b fits beside the embedder). No CPU offload, no truncated prompts, no aborted requests.
- The harness's `/health` probe (10 s timeout) failed 3 times during the new build's thinking run, 4 times during June's,
  once during the new build's fast run; the backend always recovered and no question was affected. bench4 saw 35 such
  failures on the live container; the cause (event loop busy during long LLM calls) is unrelated to the model.
- The benchmark backend's own log contains no errors and no tracebacks for either arm; its only warning is the known
  `no active spec for capability_id=legal_matter_qna tenant_id=quellex, falling back to legacy adapter` (24 occurrences
  for the new build incl. the smoke question, 43 for June), the same message bench4 logged 38 times on the live container.

## Recommendation

Keep `gemma4:12b` (the new imatrix Q4_K_M build with the MTP drafter) as the production tag. On the same 45 gold questions
it answers as well as the June build within the resolution of this benchmark — thinking mode 27 vs 26 correct (one
question gained through retrieval, none lost; p = 1.0), fast mode 20 vs 21 (one scan question lost, p = 1.0), identical
required-value and nugget scores, no draft values, no false abstentions, every unanswerable question declined — while
answering 1.6x faster end to end (median 43.7 s vs 70.2 s in thinking mode, faster on every single question; 4.7 s vs
7.8 s in fast mode) at 59 vs 30 decode tok/s. Nothing in the traces (JSON phases, tool use, query quality, citations)
regressed. A benchmark of 41 answerable questions can only detect a net shift of about six questions, so small quality
differences cannot be excluded; two runs of the same old weights (bench4 and june) already differ by two questions, and
the build difference is within that noise. Pointing production back with `ollama cp gemma4:12b-june gemma4:12b` is not
warranted; keep `gemma4:12b-june` on disk as the rollback for a few weeks of real use on the `diejuristin` corpus, which has
no gold set and was not benchmarked here. Not covered: `gemma4:12b-nodraft` (would separate the requantization from the
drafter; unnecessary for this decision) and any model other than the 12b.
