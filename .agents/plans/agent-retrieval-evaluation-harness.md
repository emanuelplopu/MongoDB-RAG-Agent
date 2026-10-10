# Feature: Golden evaluation harness for tool calls, retrieval, sections and citations

The following plan should be complete, but it is important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types and models. Import from the right files.

Plan date: 2026-10-10. Line numbers refer to `main` at commit `c032ddf`.

## Feature Description

A reliability-focused evaluation harness for RecallHub and Quellex. For a curated set of German test prompts per corpus, it runs the **real production chat path** several times per prompt and verifies four things:

1. The agent plans and executes the **correct tools with the correct arguments**.
2. It retrieves the **correct source documents**.
3. It retrieves and cites the **correct document sections** (character span, heading path, page).
4. The answer is **grounded and correct** (required facts present, no unsupported claims).

It reports per-case and aggregate metrics with confidence intervals (pass@1, pass^k, outcome consistency, Recall@k, MRR, nDCG, citation precision/recall), shows flaky cases, compares runs against a baseline, and gates regressions.

## User Story

As the owner of RecallHub/Quellex
I want to run a repeatable suite of real test prompts against the real chat path, several times each
So that I can prove with numbers that the agent always uses the right tools and arguments and finds and cites the right document sections, and see immediately when a change breaks that.

## Problem Statement

There is currently no way to measure any of the four properties. Verified facts:

- **The existing evaluation code is scaffolding.**
  - `backend/evaluation/runner.py:331-401` scores seven heuristic dimensions. For example, `citation_quality` counts `[` and `(` characters (`runner.py:355-356`).
  - `_execute_strategy` falls back to mock text (`runner.py:305-317`). Spec loading reads the wrong collection, `db["strategies"]` (`runner.py:322`), instead of `strategy_specs` (`spec_store.py:46,264`).
  - The router creates separate in-memory instances with `db=None` (`routers/evaluation.py:18-19`), so cases created through the API are never run and nothing is persisted.
  - The judge calibrator returns human scores plus noise (`judge_calibrator.py:156-172`).
  - Nothing checks tools, arguments or sections.
- **The trace loses tool arguments.** `WorkerStep.tool_input` is only `{"query", "sources"}` (`backend/agent/worker_pool.py:333`). `max_results`, `context_hint`, the resolved databases and the task origin are not recorded.
  - The safety-net task (`orchestrator.py:544-546`, id `safety_doc_search`) and the fast-mode task (`coordinator.py:1179-1198`) cannot be told apart from planned tasks.
- **Chunks have no stable section identity.**
  - `start_char`/`end_char` are running totals of contextualized text length, not offsets into the source (`src/ingestion/chunker.py:165-178`), and they are not persisted (`src/ingestion/ingest.py:1577-1585`).
  - Docling `chunk.meta.headings` and `doc_items[].prov[].page_no` are discarded.
  - Re-ingestion deletes and reinserts chunks (`ingest.py:1478-1516`), so chunk ObjectIds are not stable labels.
- **Citations are unstructured.**
  - The answer cites `[Source: "Document Title"]` (`backend/routers/prompts.py:765-776`).
  - The `sources` array returned to the client is the first 10 retrieved documents, not what the answer cited (`backend/routers/sessions.py:1064-1073`).
  - A citation cannot be mapped to a chunk or section.

Testing the four properties therefore needs prerequisites first: complete tool-call traces, stable section identity on chunks, and structured citations. Then come the dataset, the scorers, the runner and the gates.

## Solution Statement

Five layers, built bottom-up. Deterministic checks come first, and LLM judges are used only where meaning has to be judged.

1. **Observability and identity (prerequisites).**
   - Record full tool arguments and task origin in the trace.
   - Persist chunk provenance: source span in `documents.content`, heading path, pages and a stable `chunk_key`, with a backfill for existing corpora that needs no re-embedding.
   - Emit structured citations behind a config flag.
2. **Golden dataset.**
   - Versioned YAML cases in git, validated by Pydantic.
   - Gold sections are stored as chunking-independent text-quote selectors (W3C Web Annotation style). They are anchored against `documents.content` at run time and mapped onto whatever chunking is current.
3. **Scorers.**
   - Trajectory and per-field argument matching: BFCL-style accepted-value lists plus agentevals-style superset matching, forbidden calls and a call budget.
   - Retrieval metrics through `ranx` at document and section level, plus span recall, precision and IoU.
   - Citation validity, precision and recall.
   - Exact-match checks for legal tokens.
   - Reliability statistics: pass^k, outcome consistency, clustered confidence intervals and a paired baseline comparison.
4. **Harness.**
   - Drives `/api/v1/sessions/{id}/messages`, the path the UI uses (`ChatPageNew.tsx:423,513`), n times per case.
   - Persists per-repeat results and writes JSON and Markdown reports.
   - Includes a no-LLM retrieval-only harness for fast checks.
5. **Judges and gates.**
   - Calibrated binary LLM judges for vital-nugget support and faithfulness.
   - A local gate script (the repo has no `.github/workflows`) and a nightly run through the Strategy OS scheduler.

## Feature Metadata

**Feature Type**: New Capability, with enhancements to ingestion, trace and citations, and bug fixes in `backend/evaluation`
**Estimated Complexity**: High
**Primary Systems Affected**: `src/ingestion/` (chunk provenance), `backend/agent/` (trace, citations), `backend/routers/sessions.py`, `backend/routers/prompts.py`, `backend/evaluation/`, `backend/cli/`, `frontend/src/api/client.ts` (types only), `docs/10-EVALUATION_AND_JUDGE.md`
**Dependencies**: `ranx` (new, pinned, for IR metrics and significance tests). `pyyaml`, `httpx` and `typer` are already used. No new services.

---

## STATE OF THE ART (October 2026) AND DECISIONS

These are the results of the research pass, with sources under Relevant Documentation. Items marked (unverified) could not be confirmed as current.

| Concern | State of the art | Decision for this repo |
|---|---|---|
| **Tool choice and arguments** | BFCL V4 AST checking: per-parameter lists of accepted values, an empty-string entry meaning "may be omitted", strict types, normalized string compare. LangChain agentevals trajectory modes (strict, unordered, subset, superset) with per-tool argument overrides. DeepEval ToolCorrectness and ArgumentCorrectness. promptfoo trajectory assertions. BFCL label quality has been criticized (Epoch AI review 2026-09; arXiv 2607.02577). | Build an in-house comparator. Use BFCL-style accepted values and agentevals-style superset matching by default, plus forbidden calls and a call budget. The free-text `query` argument is never exact-matched. It is scored on required tokens (§ refs, case numbers, names) and by execution: what that call retrieved. |
| **Reliability ("always")** | τ-bench pass^k = E_task[C(c,k)/C(n,k)]. Inspect AI epochs with `pass_k` reducers. Outcome consistency C_out = mean_t (2p̂_t − 1)² (arXiv 2602.16666). | Run n repeats per case. Report pass@1, pass^k for k ≤ n, C_out and a flaky classification. Never auto-retry a failing case. |
| **Retrieval** | Hit@k, Recall@k, Precision@k, MRR, nDCG@k with trec_eval-validated tooling (`ranx` 0.3.21 includes significance tests). Chunking-independent span labels (LegalBench-RAG; Chroma chunking study). RAGAS ID-based context metrics break when chunks are re-cut. | Store gold spans as text-quote selectors. Map them to current chunks at eval time with a coverage threshold τ and graded relevance. Compute metrics with `ranx`, at document level and section level. In the retrieval-only harness, score vector, text and fused lists separately. |
| **Citations and grounding** | ALCE citation recall/precision via NLI. RAGAS Faithfulness and FactualCorrectness. TREC RAG nugget evaluation (vital nugget recall). HHEM-2.1 as a cheap verifier. | Deterministic first: citation validity (cited chunk was retrieved this turn), citation precision and recall against gold spans, and exact matches for §, case numbers, dates and amounts. Then vital-nugget recall and faithfulness through a calibrated judge (Phase 4). |
| **LLM judges** | Binary judges, one criterion each, reasoning before verdict, reference provided. Calibrate on 100–200 human labels with TPR/TNR ≥ 0.9 and Cohen's κ. Correct the rate with Rogan–Gladen. Pin the judge snapshot. Pointwise for gates, pairwise only for A/B tests. | Replace the fake calibrator. Judge-based metrics may only gate once a calibration exists for the pinned judge snapshot. |
| **Statistics** | SE = √(p(1−p)/n), clustered by source document (Anthropic, arXiv 2411.00640). Rule of three: 0 failures in n runs bounds the failure rate at 3/n with 95% confidence. Paired comparison against the baseline (McNemar). | Implement all of these in `reliability.py`. Every report states the rule-of-three bound so "always" has a stated meaning. |
| **Operations** | Three tiers: per change (deterministic retrieval), smoke (agent, k=3), nightly (n=8). Reasoning models ignore temperature and seed is best-effort, so reliability comes from statistics. OpenAI Evals and graders are being retired (OpenAI deprecations page). Tracing platforms (Langfuse, Phoenix) are optional. | Use a local gate script plus a nightly scheduler job. Do not adopt OpenAI Evals. Langfuse, Phoenix, Inspect AI and promptfoo are out of scope for v1 (see Notes). |

Unverified: BFCL V4 category weights; exactly how DeepEval compares argument values; GPT-5.x temperature behavior (the guidance comes from the newer migration guide); whether `seed` is supported on the Responses API.

---

## CONTEXT REFERENCES

### Relevant Codebase Files IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

**Agent and trace**
- `backend/agent/schemas.py` (lines 27-36 `TaskType`; 93-105 `TaskDefinition`; 135-149 `DocumentReference`; 204-222 `WorkerStep`; 224-262 `AgentTrace`; 339 `to_response_dict`; 423-465 `AgentModeConfig.should_use_thinking`). Why: the models to extend for full tool arguments and origin.
- `backend/agent/worker_pool.py` (lines 244-276 dispatch; 326-340 `WorkerStep` creation; 842-860 `_get_tool_name`; 290-294 and 366-380 search logging). Why: where tool input is recorded and the tool names come from.
- `backend/agent/orchestrator.py` (lines 457-560 plan JSON parsing; 544-546 safety-net `SEARCH_ALL` task; 201 temperatures). Why: task origin and planning.
- `backend/agent/coordinator.py` (line 367 entry; 1179-1198 fast mode; 569-601 telemetry). Why: fast mode creates a fixed task.
- `backend/agent/tool_gate.py` (line 27 `ToolGate`). Why: tenant-disabled tools (`TENANT_DISABLED_TOOLS=web_search,browse_web` on Quellex) must be "forbidden" in expectations.

**Chat path**
- `backend/routers/sessions.py` (lines 321-329 `SendMessageRequest`; 1002 and 1349 `FederatedAgent.process` calls; 1050 and 1501 `to_response_dict`; 1064-1081 `sources` built from `trace.all_documents[:10]`). Why: the production chat path the harness drives and where `citations` must be added.
- `backend/routers/prompts.py` (lines 765-776). Why: the default citation instruction `[Source: "Title"]`. DB-backed `prompt_templates` (8 per profile DB) also exist and must be updated together, per CLAUDE.md.

**Ingestion**
- `src/ingestion/chunker.py` (lines 120-180 HybridChunker loop; 126-131 base metadata; 150-176 metadata built and Docling meta dropped). Why: where provenance must be captured.
- `src/ingestion/ingest.py` (lines 1443-1447 metadata; 1478-1516 per-document delete; 1570-1592 chunk insert; 1772-1830 incremental mode). Why: where provenance is persisted. Incremental mode skips existing sources and never deletes missing files.

**Evaluation**
- `backend/evaluation/models.py` (lines 105-120 `EvaluationTestCase`, then `EvaluationRun`). Why: models to extend, not replace.
- `backend/evaluation/runner.py` (55 `run_evaluation`; 196 `_evaluate_single_case`; 257-317 `_execute_strategy` mock fallback; 319-330 wrong spec collection; 403 `_store_result`; 411-425 `_check_sources`). Why: bugs to fix and structure to extend.
- `backend/evaluation/test_case_manager.py` (line 29 version bump fields; 219 `check_stale` queries a nonexistent `source_id`). Why: bug fixes.
- `backend/evaluation/judge_calibrator.py` (lines 21-23 thresholds; 156-172 fake judge). Why: replaced in Phase 4.
- `backend/routers/evaluation.py` (lines 18-19 separate instances; routes at 91-193). Why: bug fix.
- `scripts/migrate_evaluation_collections.py` (lines 21-41). Why: the pattern for creating evaluation collection indexes.

**CLI**
- `backend/cli/main.py` (lines 118-128 registers command modules by import) and `backend/cli/eval_commands.py` (lines 1-40 Typer sub-app pattern, `run_async`, `OutputFormatter`). Why: the pattern for new `golden` commands.

**Tests**
- `backend/tests/conftest.py` (lines 18-28 `litellm` stub that raises on real calls; 122-195 `mock_db`; 218-245 `app` and auth overrides). Why: unit tests must never call a real LLM.
- `backend/tests/integration/test_api_integration.py` (lines 16-19 `RUN_INTEGRATION_TESTS` gate). Why: the gating pattern for live tests.
- `backend/tests/strategy/_fakes.py`. Why: in-memory Mongo fakes to mirror.
- `test_scripts/comprehensive_e2e_test.py` (lines 15-78) and `test_scripts/test_full_pipeline_e2e.py`. Why: earlier ad-hoc e2e attempts. The latter shows how to parse the SSE stream.

**Docs**
- `docs/10-EVALUATION_AND_JUDGE.md`. It has drifted from the code (documents `repeats`, admin auth, an `evaluation_test_cases` collection and different thresholds) and must be rewritten.

### New Files to Create

- `backend/evaluation/golden/__init__.py`
- `backend/evaluation/golden/models.py` — `GoldenCase`, `ToolExpectation`, `ArgRule`, `SpanSelector`, `Nugget`, `DatasetManifest`.
- `backend/evaluation/golden/anchoring.py` — selector resolution against `documents.content`, and span-to-chunk relevance mapping.
- `backend/evaluation/golden/loader.py` — YAML load, validate and upsert.
- `backend/evaluation/scorers/__init__.py`
- `backend/evaluation/scorers/normalize.py` — German-aware normalization and legal token parsers.
- `backend/evaluation/scorers/tool_trajectory.py`
- `backend/evaluation/scorers/retrieval.py`
- `backend/evaluation/scorers/citations.py`
- `backend/evaluation/scorers/answer.py`
- `backend/evaluation/reliability.py`
- `backend/evaluation/harness/__init__.py`
- `backend/evaluation/harness/agent_client.py`
- `backend/evaluation/harness/retrieval_runner.py`
- `backend/evaluation/harness/run_golden.py`
- `backend/evaluation/report.py`
- `backend/agent/citations.py` — numbered evidence ledger and marker parser.
- `backend/cli/golden_commands.py`
- `scripts/backfill_chunk_provenance.py`
- `backend/evaluation/datasets/test-law-v1/dataset.yaml` and `cases/*.yaml`
- `backend/evaluation/datasets/parhelion-v1/dataset.yaml` and `cases/*.yaml`
- Tests in `backend/tests/evaluation/`: `test_golden_models.py`, `test_anchoring.py`, `test_normalize.py`, `test_tool_trajectory.py`, `test_retrieval_scorer.py`, `test_citations_scorer.py`, `test_reliability.py`, `test_run_golden.py`. Plus `backend/tests/test_chunk_provenance.py`, `backend/tests/test_backfill_provenance.py`, `backend/tests/agent/test_trace_tool_input.py`, `backend/tests/agent/test_citation_ledger.py` and `backend/tests/integration/test_golden_harness.py`.

### Relevant Documentation YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [τ-bench paper, pass^k definition](https://arxiv.org/html/2406.12045). Why: the unbiased pass^k estimator.
- [Inspect AI metrics and epochs](https://inspect.aisi.org.uk/metrics.html). Why: reducer semantics (`pass_k`, `at_least_k`) and clustered stderr to mirror.
- [BFCL AST evaluation blog](https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html). Why: per-parameter accepted values and normalization rules.
- [LangChain agentevals](https://github.com/langchain-ai/agentevals). Why: trajectory match modes and `tool_args_match_overrides` semantics.
- [Tool-calling benchmark validity audit (arXiv 2607.02577)](https://arxiv.org/html/2607.02577v1) and the [Epoch AI BFCL review](https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review). Why: label-quality pitfalls to avoid in our own cases.
- [Agent reliability metrics (arXiv 2602.16666)](https://arxiv.org/html/2602.16666v3). Why: outcome consistency C_out.
- [ranx](https://github.com/AmenRa/ranx). Why: Qrels/Run API, metrics, `compare` with significance tests.
- [LegalBench-RAG](https://arxiv.org/html/2408.10343v1) and the [Chroma chunking evaluation](https://www.trychroma.com/research/evaluating-chunking). Why: span-level, chunking-independent retrieval labels.
- [W3C Web Annotation data model, TextQuoteSelector](https://www.w3.org/TR/annotation-model/#text-quote-selector). Why: selector format for gold spans.
- [ALCE citation evaluation](https://arxiv.org/html/2305.14627). Why: citation recall/precision definitions.
- [RAGAS faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) and [testset generation](https://docs.ragas.io/en/stable/getstarted/rag_testset_generation/). Why: claim-level faithfulness and the draft-case query mix.
- [TREC 2025 RAG overview, nugget evaluation](https://arxiv.org/pdf/2603.09891). Why: vital/okay nugget recall.
- [Adding error bars to evals (arXiv 2411.00640)](https://arxiv.org/pdf/2411.00640). Why: clustered standard errors and paired comparisons.
- [Reporting LLM-judge evaluations (arXiv 2511.21140)](https://arxiv.org/html/2511.21140v1). Why: Rogan–Gladen correction with judge TPR/TNR.
- [docling-core DocChunk](https://raw.githubusercontent.com/docling-project/docling-core/main/docling_core/transforms/chunker/doc_chunk.py). Why: `meta.headings` and `meta.doc_items[].prov[].page_no`.
- [OpenAI API deprecations](https://developers.openai.com/api/docs/deprecations). Why: OpenAI Evals and graders are being retired, so do not depend on them.

### Patterns to Follow

**Naming:** snake_case modules and functions, PascalCase Pydantic models, kebab-case dataset IDs (`test-law-v1`), Typer command names in kebab-case.

**Models:** Pydantic v2 `BaseModel` with `Field(default_factory=...)`, mirroring `backend/evaluation/models.py:105-120`. Extend existing models with optional fields rather than forking them (CLAUDE.md rule 7).

**Mongo:** new async DB code uses `pymongo.AsyncMongoClient` (CLAUDE.md rule 3). Collection names flow through settings (`backend/core/config.py`), never hard-coded in random modules.

**CLI:** create `golden_app = typer.Typer(name="golden", ...)`, register it with `app.add_typer(...)` in the new module, and import that module in `backend/cli/main.py:124-128`. Use `run_async` and `OutputFormatter` from `backend.cli.main` (see `eval_commands.py:17-24`).

**Errors and logging:** never swallow exceptions. A case that errors or times out is a **failed repeat** with `error` recorded, never a skipped one. Log with context (dataset, case id, repeat, trace id). Never log API keys or user emails.

**Tests:** `asyncio_mode = "auto"` (`pyproject.toml:47-50`). Unit tests use synthetic traces and fakes and must pass with the `litellm` stub that raises. Live tests are gated by `RUN_INTEGRATION_TESTS=true`.

---

## IMPLEMENTATION PLAN

### Phase 0: Prerequisites (observability and identity)

Without these, tool arguments, sections and citations cannot be scored. Each task is useful on its own, because it improves traces and the UI too.

### Phase 1: Golden dataset schema and tooling

Schema, anchoring, loader, CLI and the first two datasets. **Authoring the cases is the critical path.** It needs a domain reviewer for the German legal corpus.

### Phase 2: Deterministic scorers

Normalization, trajectory and arguments, retrieval, citations, deterministic answer checks, and reliability statistics.

### Phase 3: Harness, persistence, reporting, CLI

Agent harness over HTTP, the retrieval-only harness, the runner with repeats, persistence, reports and baselines.

### Phase 4: Judge-based metrics and calibration

Real binary judges, human calibration, and corrected rates.

### Phase 5: Gates, scheduling and docs

Gate script, nightly run and the rewrite of `docs/10`.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Phase 0

### UPDATE `backend/evaluation/runner.py`, `backend/routers/evaluation.py`, `backend/evaluation/test_case_manager.py` (fix wiring bugs)

- **IMPLEMENT**:
  - The router uses one shared `TestCaseManager` and passes it to `EvaluationRunner`, so `POST /test-cases` cases are visible to `POST /run`.
  - Inject the real DB through FastAPI dependency or app state from the lifespan, instead of `db=None`.
  - `_load_strategy_spec` loads through `spec_store` (`strategy_specs`, `spec_data.strategy_id`).
  - The leaderboard filters by `dataset_id`.
  - `check_stale` queries `documents.source`.
  - Remove the silent mock fallback in production: if no executor is configured, raise a clear error.
- **PATTERN**: `backend/agent/strategy/spec_store.py:46,264`.
- **GOTCHA**: Keep the existing tests green. Some tests rely on `db=None`, so keep a test-only path explicit.
- **VALIDATE**: `uv run pytest backend/tests -k "evaluation" -v`

### UPDATE `backend/agent/schemas.py` and `backend/agent/worker_pool.py` (full tool arguments and origin)

- **IMPLEMENT**:
  - Add `origin: Literal["planned","safety_net","follow_up","fast_mode","refine"] = "planned"` to `TaskDefinition`.
  - In `WorkerStep`, set `tool_input` to the full `TaskDefinition.model_dump()`: type, query, sources, max_results, context_hint, priority, depends_on, origin. Also add `resolved` with the databases actually searched, `search_type`, `match_count`, `num_candidates` and any metadata filters actually applied.
  - Add `rank` (1-based position in that step's result list) and, where available, `chunk_index`, `chunk_key` and `source_span` to `DocumentReference.metadata`.
- **PATTERN**: `worker_pool.py:326-340`.
- **GOTCHA**: Set `origin` where tasks are created: the orchestrator plan (`orchestrator.py:457-560`) is `planned`, the safety net (`orchestrator.py:544-546`) is `safety_net`, evaluation follow-ups are `follow_up`, and coordinator fast mode (`coordinator.py:1179-1198`) is `fast_mode`. `use_enum_values=True` is set on these models, so compare string values.
- **VALIDATE**: `uv run pytest backend/tests/agent/test_trace_tool_input.py -v` (new; asserts the keys and origin for a planned task, the safety-net task and a fast-mode task, built through the same constructors).

### UPDATE `backend/agent/schemas.py` (`AgentTrace.to_response_dict`, line 339)

- **IMPLEMENT**: Serialize `initial_plan` and `evaluation_history`, so the planned tool calls (including tasks that were never executed) are visible to the harness.
- **GOTCHA**: The payload size grows. Keep excerpts truncated as they are today, and do not include embeddings.
- **VALIDATE**: `uv run pytest backend/tests/agent/test_trace_tool_input.py -v`

### UPDATE `src/ingestion/chunker.py` (capture provenance)

- **IMPLEMENT**:
  - For each Docling chunk, add `metadata.heading_path = list(chunk.meta.headings or [])`.
  - Add `metadata.pages`: sorted unique `prov.page_no` over `chunk.meta.doc_items[*].prov`.
  - Keep the raw, non-contextualized `chunk.text` on `DocumentChunk` as a new transient attribute `raw_text`. It is not persisted.
  - Remove or rename the fake `start_char`/`end_char` estimates (lines 165-178) so nobody mistakes them for offsets.
- **GOTCHA**: The simple fallback chunker (no `DoclingDocument`, lines 133-139) has no meta. Set `metadata.provenance = "fallback"` there and `"docling"` on the main path.
- **VALIDATE**: `uv run pytest backend/tests/test_chunk_provenance.py -v` (new; fake chunk objects built with `SimpleNamespace(meta=SimpleNamespace(headings=[...], doc_items=[...]))`).

### UPDATE `src/ingestion/ingest.py` (persist span and stable key, around lines 1570-1592)

- **IMPLEMENT**:
  - Before insert, locate each chunk's `raw_text` inside the document's stored `content`, which is the canonical text. Search forward from the previous chunk's end. Try an exact match first, then a whitespace- and markdown-normalized match that keeps an index map back to the original offsets.
  - Persist `metadata.source_span = [start, end]`, or `None` if not found.
  - Persist `metadata.chunk_key = sha1(f"{source}|{start}|{end}|{sha1(raw_text)}")[:24]`.
  - Count unresolved spans in the ingestion stats.
- **PATTERN**: `ingest.py:1577-1585` chunk dict.
- **GOTCHA**: `content` is Docling's markdown export, while `chunk.text` is item text (tables and escaping differ). Normalize both sides the same way. Target at least 95% located on Docling-path documents. Never fail ingestion on a missed span.
- **VALIDATE**: `uv run pytest backend/tests/test_chunk_provenance.py -v` (add markdown fixtures with headings, a table, umlauts and `§`).

### CREATE `scripts/backfill_chunk_provenance.py`

- **IMPLEMENT**:
  - CLI flags: `--db rag_test_law --dry-run|--apply --limit N`.
  - For each document, load `content` and its chunks ordered by `chunk_index`.
  - Strip the contextualization prefix. `contextualize()` prepends heading lines; match the longest chunk-content suffix that occurs in `content`.
  - Compute `source_span`, a `heading_path` derived from the markdown headings before the span start, and `chunk_key`. Write with bulk `update_one` operations.
  - Print a coverage report. **No re-embedding.** Pages stay absent for backfilled chunks.
- **PATTERN**: `scripts/migrate_strategy_os_collections.py` for script structure. Use `pymongo.AsyncMongoClient`.
- **GOTCHA**: Run against `rag_test_law` (3,075 chunks) first, then `rag_parhelion` (432,417 chunks; batch it and make it resumable by `_id`). Stop the ingestion worker during `--apply`.
- **VALIDATE**: `uv run pytest backend/tests/test_backfill_provenance.py -v`, then `uv run python scripts/backfill_chunk_provenance.py --db rag_test_law --dry-run`. Expect coverage ≥ 95%.

### CREATE `backend/agent/citations.py`, UPDATE the synthesis prompt and `sessions.py` (structured citations)

- **IMPLEMENT**:
  - Add `CITATION_FORMAT: Literal["legacy","numbered"] = "legacy"` in `backend/core/config.py`.
  - In `numbered` mode, give the synthesizer a numbered evidence list `[1] Title — excerpt` built from `trace.all_documents`, and instruct it to cite with `[n]`.
  - After synthesis, `parse_citations(text, ledger)` returns `[{marker, chunk_id, document_id, source, chunk_key, source_span, valid}]`. Unknown markers have `valid=False`.
  - Add `citations` to the assistant message payload in both the non-stream and stream paths (`sessions.py` near 1064 and 1516). Keep `sources` unchanged for UI compatibility.
  - In `legacy` mode, `parse_citations` maps `[Source: "Title"]` to every retrieved chunk with that title. These are title-level and flagged `granularity="document"`.
- **PATTERN**: `backend/agent/strategy/models.py:66-74` `CitationRef`. Reuse the field names (`source_id`, `span_start`, `span_end`, `quote`) where they fit.
- **GOTCHA**:
  - Per CLAUDE.md, update **both** the default prompt (`routers/prompts.py:765-776`) and the DB-backed `prompt_templates` in each profile DB, with a version bump or a migration script.
  - Check how `ChatPageNew.tsx` renders citation text before switching the default.
  - Update `SessionMessage` in `frontend/src/api/client.ts` (around lines 811-818) with an optional `citations` field.
- **VALIDATE**: `uv run pytest backend/tests/agent/test_citation_ledger.py -v` and `npm --prefix frontend run build`

### Phase 1

### CREATE `backend/evaluation/golden/models.py`

- **IMPLEMENT**: Pydantic v2 models.
  - `DatasetManifest{id, version, tenant: Literal["recallhub","quellex"], profile, database, language="de", default_agent_mode: Literal["fast","thinking"], owner, description}`
  - `ArgRule{kind: Literal["exact","one_of","regex","range","tokens_all","tokens_any","subset","superset","ignore"], values: list|None, pattern: str|None, min: float|None, max: float|None, allow_omitted: bool=False}`
  - `ToolExpectation{tool: str  # e.g. "search_profile_documents"; required: bool=True; min_count: int=1; args: dict[str, ArgRule]; origin_any_of: list[str]|None}`
  - `SpanSelector{source: str, content_hash: str|None, quote: str, prefix: str="", suffix: str="", heading_path: list[str]|None, page: int|None, grade: Literal[1,2]=2, equivalent_sources: list[str]=[]}`. `equivalent_sources` covers identical files in several folders (for example the `_anon` and dated copies in `Test_Data`).
  - `Nugget{text: str, vital: bool=True}`
  - `GoldenCase{id, version, status: Literal["draft","approved","retired"], prompt, agent_mode: Literal["fast","thinking"]|None, tags: list[str], category, expected_tools: list[ToolExpectation], forbidden_tools: list[str], max_tool_calls: int|None, trajectory_mode: Literal["superset","unordered","strict"]="superset", gold_documents: list[str], gold_spans: list[SpanSelector], nuggets: list[Nugget], must_include: list[str], must_not_include: list[str], reference_answer: str|None, unanswerable: bool=False, retrieval_queries: list[str]}`. `retrieval_queries` feed the retrieval-only harness; when empty, the prompt is used.
- **GOTCHA**:
  - Add an optional `golden_ref: {dataset_id, case_id, case_version}` to `EvaluationTestCase` instead of a parallel collection.
  - Validators: `approved` cases need at least one `expected_tools` entry and at least one gold document or span, unless `unanswerable`.
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_golden_models.py -v`

### CREATE `backend/evaluation/golden/anchoring.py`

- **IMPLEMENT**:
  - `resolve(selector, content) -> list[tuple[int,int]]`: exact quote search. If there are several hits, disambiguate with prefix and suffix. Otherwise fall back to a normalized match (`scorers/normalize.py`) with an offset map. Returns an empty list when the selector is stale.
  - `chunk_relevance(gold_spans, chunks) -> dict[chunk_key, grade]`: a chunk counts as relevant at grade g if `|gold ∩ chunk| / |gold| ≥ τ` (default 0.5), or if the chunk lies entirely inside a gold span.
  - `span_metrics(gold_spans, retrieved_spans)` returns span recall `|E∩T|/|E|`, span precision `|E∩T|/|T|` and IoU.
- **GOTCHA**: Spans are in `documents.content` coordinates. Chunks without `source_span` cannot count toward section metrics. Report how many were missing rather than silently scoring zero.
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_anchoring.py -v` (German fixtures: umlauts, ß, `§ 823 Abs. 1 BGB`, a duplicated quote, a quote that crosses a markdown table).

### CREATE `backend/evaluation/golden/loader.py`

- **IMPLEMENT**:
  - `load_dataset(path) -> (DatasetManifest, list[GoldenCase])`, using `yaml.safe_load` and Pydantic validation.
  - `anchor_dataset(db, cases)` resolves every selector against the live DB and returns the stale ones.
  - `upsert_to_db(db, manifest, cases)` writes `strategy_test_cases` entries with `golden_ref`, using `TestCaseManager` version bumps.
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_golden_models.py -v`

### CREATE `backend/cli/golden_commands.py` and UPDATE `backend/cli/main.py:124-128`

- **IMPLEMENT**:
  - `quellexctl golden validate --dataset test-law-v1`: schema check plus anchoring. Exits non-zero if any approved case is stale.
  - `quellexctl golden draft --dataset test-law-v1 --n 30`: samples chunks stratified by folder and file type. An LLM writes German questions (about 50% single-hop, 25% multi-hop specific, 25% multi-hop abstract), quotes and nuggets. The command writes `status: draft` YAML for human review and never auto-approves.
  - `quellexctl golden import --dataset ...`: upserts into the DB.
- **PATTERN**: `backend/cli/eval_commands.py:17-24`.
- **GOTCHA**: The draft generator must store quotes verbatim from `documents.content` and must paraphrase the question (synthetic questions tend to copy source wording).
- **VALIDATE**: `uv run quellexctl golden validate --dataset test-law-v1`

### CREATE `backend/evaluation/datasets/test-law-v1/` and `parhelion-v1/`

- **IMPLEMENT**: Start with **≥ 60 approved test-law cases** and **≥ 40 Parhelion cases**, reviewed by the domain owner. Category mix per dataset:
  - single-document fact lookup
  - exact legal tokens (§, case number, dates, amounts)
  - multi-document comparison
  - email thread
  - scanned or OCR document
  - folder-scoped (for example only `Test3_Sozialbau`)
  - **unanswerable** (must abstain)
  - expected `search_profile_documents` vs `search_all_sources`, and forbidden `web_search`/`browse_web` on Quellex
- **GOTCHA**: Grow towards 150–300 cases over time. At n=60 a 90% pass rate has a 95% CI of about ±7.6 points.
- **VALIDATE**: `uv run quellexctl golden validate --dataset test-law-v1` and `... --dataset parhelion-v1` (0 stale).

### Phase 2

### CREATE `backend/evaluation/scorers/normalize.py`

- **IMPLEMENT**:
  - `norm_text`: NFC, casefold, collapse whitespace and punctuation, configurable `ß→ss`, optional umlaut transliteration equivalence.
  - `parse_paragraph_refs("§ 823 Abs. 1 BGB") -> {("823","1",None,"BGB")}`
  - `parse_case_numbers` (Austrian and German Aktenzeichen patterns, for example `3 Cg 123/21x`)
  - `parse_dates` (`dd.mm.yyyy`, `d. Monat yyyy` in German, returned as ISO)
  - `parse_amounts` (`1.234,56 €` becomes `Decimal("1234.56")`)
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_normalize.py -v`

### CREATE `backend/evaluation/scorers/tool_trajectory.py`

- **IMPLEMENT**: `score_trajectory(case, trace) -> TrajectoryResult`.
  - Collect the executed steps from `trace["worker_steps"]` and the planned steps from `initial_plan`.
  - For each `ToolExpectation`, find at least `min_count` matching steps under `trajectory_mode`. Each `ArgRule` is evaluated on the normalized `tool_input`, and `allow_omitted` accepts missing or default values.
  - The case fails if any forbidden tool is executed or `max_tool_calls` is exceeded.
  - Return per-expectation verdicts with human-readable reasons (expected vs actual).
  - Report safety-net and fast-mode steps separately. A case where only the safety net found the documents is a **planning failure** even if retrieval succeeded.
- **PATTERN**: agentevals superset semantics and BFCL accepted-value lists (see docs).
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_tool_trajectory.py -v` (cases: exact match, an extra harmless call under superset, a missing call, a forbidden `web_search`, a wrong `max_results` range, token rules on `query`, safety-net-only).

### CREATE `backend/evaluation/scorers/retrieval.py`

- **IMPLEMENT**:
  - Build `ranx.Qrels` from anchored gold spans: chunk level via `chunk_relevance` with graded relevance, and document level via `source` plus `equivalent_sources`.
  - Build `ranx.Run` from each step's ranked `DocumentReference` list, the union per turn, and, in the retrieval harness, the separate vector, text and fused lists.
  - Metrics: `hit_rate@{1,5,10}`, `recall@{5,10,20}`, `precision@{5,10}`, `mrr`, `ndcg@10`, plus span recall, precision and IoU.
  - For document-level metrics, deduplicate chunks to their first-ranked document.
- **IMPORTS**: `from ranx import Qrels, Run, evaluate, compare`. Run `uv add ranx` and pin the exact version.
- **GOTCHA**: `ranx` uses Numba. Check the first-import time inside the backend Docker image (`.\build-backend.ps1 -Base` is needed if it adds a dependency layer).
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_retrieval_scorer.py -v` (hand-computed MRR and nDCG values).

### CREATE `backend/evaluation/scorers/citations.py`

- **IMPLEMENT**:
  - citation validity = valid markers ÷ markers
  - citation precision = cited chunks overlapping any gold span ÷ cited chunks
  - citation recall = required gold spans covered by at least one citation ÷ required gold spans
  - document-level variants for `legacy` title citations
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_citations_scorer.py -v`

### CREATE `backend/evaluation/scorers/answer.py`

- **IMPLEMENT**:
  - Deterministic checks: `must_include` and `must_not_include` after normalization, plus a structured comparison of § refs, case numbers, dates and amounts using the parsers.
  - Unanswerable cases pass only if the answer abstains (configurable German and English abstention patterns) and cites nothing.
  - Leave a `JudgeHook` protocol for Phase 4.
- **VALIDATE**: `uv run pytest backend/tests/evaluation -k answer -v`

### CREATE `backend/evaluation/reliability.py`

- **IMPLEMENT**:
  - `pass_hat_k(n, c, k) = comb(c,k)/comb(n,k)`
  - `pass_at_1 = c/n`
  - `outcome_consistency = mean((2p̂−1)²)`
  - `classify(c, n)` returns stable_pass, stable_fail or flaky
  - Wilson interval per case, and a cluster bootstrap CI over source documents for aggregates
  - `mcnemar(baseline, candidate)` on paired case outcomes
  - `rule_of_three(n_clean_runs)` returns 3/n
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_reliability.py -v`. Include `pass_hat_k(8,6,4) == 15/70`, `pass_hat_k(5,5,5) == 1` and `pass_hat_k(5,4,5) == 0`.

### Phase 3

### CREATE `backend/evaluation/harness/agent_client.py`

- **IMPLEMENT**: An `httpx.AsyncClient` against `--base-url`: Quellex is `http://localhost:11001`, RecallHub is `http://localhost:11000`.
  - Auth uses the `X-API-Key` header from the env var `EVAL_API_KEY`. It is never in code, committed config or `VITE_*` variables.
  - Each repeat creates a fresh session (`POST /api/v1/sessions`) and sends `POST /api/v1/sessions/{id}/messages` with `{content, agent_mode, language}`.
  - Returns `{text, agent_trace, strategy_trace, sources, citations, latency_ms, error}`.
  - Optional `--in-process` mode calls `FederatedAgent.process` directly, for environments without a running server.
- **GOTCHA**:
  - The active profile is **process-global** (`sessions.py:941-950`). At the start of a dataset run, switch with `POST /api/v1/profiles/switch` (needs auth) and verify with GET.
  - Hold a run lock document (an `eval_run_locks` collection, named through settings) so two runs cannot interleave, and refuse to run while the lock is held.
  - Warn that real users of the same backend see the profile switch.
- **VALIDATE**: `RUN_INTEGRATION_TESTS=true uv run pytest backend/tests/integration/test_golden_harness.py -v` (3 cases against the running stack).

### CREATE `backend/evaluation/harness/retrieval_runner.py`

- **IMPLEMENT**: For each case's `retrieval_queries`, call the search service functions behind `backend/routers/search.py` directly (hybrid, plus vector-only and text-only) and capture per-list ranks.
  - Cache query embeddings on disk, keyed by `(embedding_model, dimension, text)`, so repeat runs make no API calls.
  - No LLM is involved.
- **GOTCHA**: The current code often under-requests ANN candidates (CLAUDE.md: `numCandidates ≥ 20 × limit`). Record `numCandidates` in the report so tuning is measurable.
- **VALIDATE**: `uv run quellexctl golden retrieval --dataset test-law-v1 --limit 5`

### CREATE `backend/evaluation/harness/run_golden.py` and EXTEND `EvaluationRun`

- **IMPLEMENT**: `run_dataset(dataset_id, repeats, mode, concurrency, limit, baseline_run_id)`.
  - Loop over approved cases × repeats, call `agent_client`, run all scorers, and persist one record per repeat.
  - Add optional fields to `EvaluationRun` in `backend/evaluation/models.py`: `run_id`, `dataset_id`, `dataset_version`, `case_id`, `case_version`, `repeat_index`, `git_sha`, `llm_model`, `embedding_model`, `judge_model`, `scores: dict[str,float]`, `verdicts: dict[str,bool]`, `trace_id`, `error`.
  - Store into `evaluation_results` and add indexes on `(run_id, case_id, repeat_index)` and `(dataset_id, created_at)` in `scripts/migrate_evaluation_collections.py`.
  - Respect timeouts and budgets from `backend/core/config.py`. Runs are resumable by `run_id`.
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_run_golden.py -v` (fake client that returns canned traces).

### CREATE `backend/evaluation/report.py`

- **IMPLEMENT**: JSON plus Markdown report.
  - Headline table with tool pass^k, document Recall@10, section Recall@10, MRR, nDCG@10, citation precision and recall, and answer pass, each with a 95% CI and the rule-of-three line.
  - Per-category breakdown, the flaky-case list, and the worst cases with diffs: expected vs actual arguments, missing spans with heading path and page, invalid citations.
  - Baseline comparison with McNemar p-values.
- **VALIDATE**: `uv run pytest backend/tests/evaluation/test_run_golden.py -v`

### UPDATE `backend/cli/golden_commands.py` (run and report)

- **IMPLEMENT**:
  - `quellexctl golden run --dataset test-law-v1 --repeats 5 --mode thinking --base-url http://localhost:11001 --out reports/` prints the run id and report path.
  - `quellexctl golden report --run <id> [--baseline <id>]`
  - `quellexctl golden retrieval --dataset ...`
- **VALIDATE**: `uv run quellexctl golden run --dataset test-law-v1 --repeats 3 --limit 5`

### Phase 4

### UPDATE `backend/evaluation/judge_calibrator.py` and ADD judges in `scorers/answer.py`

- **IMPLEMENT**:
  - Binary judges through the existing LLM helper or litellm with a **pinned model snapshot** taken from config. Each judge writes its reasoning before the verdict and returns JSON.
  - Judges: `nugget_supported(answer, nugget)` for vital and okay nuggets, `claim_faithful(claim, cited_chunks)` and `abstained(answer)`.
  - Remove the noise-based fake (lines 156-172).
  - Calibration: export about 150 judged items to `backend/evaluation/datasets/<id>/calibration/*.yaml` for human labels. Compute TPR, TNR and Cohen's κ, and store them in `judge_calibrations`.
  - Reports show the Rogan–Gladen corrected rate `(p_obs + TNR − 1)/(TPR + TNR − 1)` with a bootstrap CI.
  - Judge metrics are **informational, not gating**, until a calibration with TPR and TNR ≥ 0.9 exists for the current judge snapshot.
- **GOTCHA**: Only OpenAI keys are configured. A same-family judge risks self-preference, so use a different snapshot or size than the generator and rely on calibration.
- **VALIDATE**: `uv run pytest backend/tests -k judge -v` (mocked litellm only).

### Phase 5

### CREATE `scripts/eval-gate.ps1`

- **IMPLEMENT**:
  1. `uv run pytest backend/tests/evaluation -q`
  2. The retrieval harness on all approved cases. Fail if document Recall@10 or section Recall@10 drops below the stored baseline minus its CI.
  3. The agent smoke subset (cases tagged `smoke`, k=3). Fail if any stable-pass case regresses or pass^3 drops below the baseline CI.
  - Exit codes: 0 pass, 1 regression, 2 infrastructure error.
- **VALIDATE**: `.\scripts\eval-gate.ps1 -Dataset test-law-v1`. Then inject a regression (temporarily force `max_results=1`) and confirm exit code 1.

### UPDATE `backend/scheduler/` (nightly run)

- **IMPLEMENT**: A scheduled job type `golden_eval` that runs n=8 nightly when no chat activity is recent (reuse `activity_tracker` / `runtime_signals` idle detection) and records the baseline for the current git SHA.
- **PATTERN**: `backend/scheduler/scheduler_daemon.py` job registration.
- **VALIDATE**: `uv run pytest backend/tests -k scheduler -v`

### UPDATE `docs/10-EVALUATION_AND_JUDGE.md`, `docs/04-EMBEDDING_AND_QUERY_SYSTEM.md`

- **IMPLEMENT**:
  - Rewrite docs/10 to match the code: collections, repeats, auth, thresholds, golden dataset authoring, metrics and their formulas, gate usage, and what "always" means statistically.
  - Document the new chunk provenance fields in docs/04.
- **VALIDATE**: manual review. `AGENTS.md` and `CLAUDE.md` change only if conventions change, and then both change together.

---

## TESTING STRATEGY

### Unit Tests

- Every scorer runs on synthetic traces and chunk lists. No network, and no LLM (the `litellm` stub raises).
- Reliability math is checked against hand-computed values.
- Anchoring and normalization are tested on German legal fixtures.
- Trace building is tested for planned, safety-net and fast-mode tasks.
- The citation parser is tested in both formats.

### Integration Tests

- Gated by `RUN_INTEGRATION_TESTS=true`. Three golden cases run against the live stack on port 11001 with `--repeats 2`. They assert that the report is produced, that every repeat has a trace with full `tool_input`, and that retrieval metrics are computed.
- Backfill dry-run against `rag_test_law` with coverage ≥ 95%.

### Edge Cases

- **Duplicate files across folders.** For example `Test1_Austrian_Civi_Litigation` vs `_anon` vs `_22.4.2026`. Handle these with `equivalent_sources` and content-hash groups.
- **Re-ingested documents.** ObjectIds change, but labels survive through selectors and `chunk_key`.
- A quote that appears several times in one document.
- A quote that crosses a table or heading boundary.
- OCR'd images.
- Email threads with quoted replies.
- Answers that come only from the safety-net search.
- Tools disabled by the tenant (`TENANT_DISABLED_TOOLS`).
- `agent_mode` auto-selection: pin the mode per case, because `should_use_thinking` is a keyword heuristic.
- Timeouts and LLM errors count as failures.
- Empty retrieval.
- Unanswerable questions.
- Profile switching while another user is active (the lock refuses to run).
- Chunks with no `source_span`, which are reported rather than scored as zero.

---

## VALIDATION COMMANDS

### Level 1: Syntax & Style

```powershell
uv run ruff check backend src scripts
uv run black --check backend src scripts
```

### Level 2: Unit Tests

```powershell
uv run pytest backend/tests/evaluation -v
uv run pytest backend/tests/agent/test_trace_tool_input.py backend/tests/agent/test_citation_ledger.py backend/tests/test_chunk_provenance.py backend/tests/test_backfill_provenance.py -v
uv run pytest backend/tests -v
```

### Level 3: Integration Tests

```powershell
$env:RUN_INTEGRATION_TESTS="true"; uv run pytest backend/tests/integration/test_golden_harness.py -v
```

### Level 4: Manual Validation

```powershell
uv run python scripts/backfill_chunk_provenance.py --db rag_test_law --dry-run
uv run quellexctl golden validate --dataset test-law-v1
uv run quellexctl golden retrieval --dataset test-law-v1
uv run quellexctl golden run --dataset test-law-v1 --repeats 5 --mode thinking --base-url http://localhost:11001
npm --prefix frontend run build
.\build-backend.ps1 -All
```

### Level 5: Additional Validation

- Inject regressions and confirm the gate catches each one: force `max_results=1`, disable the text index in hybrid search, and remove the citation instruction.
- Compare two identical runs. Check that C_out and the flaky list are stable within their CIs.

---

## ACCEPTANCE CRITERIA

- [ ] All evaluation wiring bugs are fixed. `POST /test-cases` cases run via `POST /run`, and results persist.
- [ ] 100% of `worker_steps` carry full `tool_input` (including `max_results`, `resolved`, `origin`). The trace includes `initial_plan` and `evaluation_history`.
- [ ] Newly ingested Docling-path chunks have `heading_path`, `pages`, `source_span` (≥ 95%) and `chunk_key`. After backfill, ≥ 95% of `rag_test_law` chunks have `source_span`.
- [ ] Numbered citations are available behind `CITATION_FORMAT`. On the smoke set, 0 invalid markers, and ≥ 90% of sourced answers carry structured citations.
- [ ] `test-law-v1` has ≥ 60 and `parhelion-v1` ≥ 40 approved, human-reviewed cases with 0 stale selectors.
- [ ] `quellexctl golden run --repeats 5` produces a report with pass@1, pass^5, C_out, document- and section-level Recall@k/MRR/nDCG@10, span metrics, citation precision/recall and answer pass. Each comes with a 95% CI, a flaky list and per-case diffs.
- [ ] The retrieval-only harness runs with no LLM calls after the embedding cache is warm.
- [ ] Reliability math matches hand-computed values in unit tests.
- [ ] `eval-gate.ps1` exits 1 on each injected regression and 0 on a clean baseline.
- [ ] Judge metrics report calibrated TPR/TNR/κ and corrected rates, and are non-gating until calibrated.
- [ ] `docs/10` and `docs/04` match the code. The frontend builds with the new optional `citations` type.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order
- [ ] Each task validation passed immediately
- [ ] All validation commands executed successfully
- [ ] Full test suite passes (unit + integration)
- [ ] No linting or type checking errors
- [ ] Manual testing confirms the feature works
- [ ] Acceptance criteria all met
- [ ] Code reviewed for quality and maintainability

---

## NOTES

- **What "always" means.** No finite test proves "always". With n clean repeats of a case, the 95% upper bound on its failure rate is about 3/n. pass^k is the right headline: a case with 95% per-run success scores only 0.77 at k=5. Reports must show these numbers instead of a single pass/fail.
- **Why the real chat path and not the Strategy OS DAG.** In production the DAG currently cannot produce a grounded answer. `runner.run()` gets no `query`, `RetrieveExecutor` has no `FederatedSearch`, and `_execute_standard_search` returns `[]` (`coordinator.py:717-780`, `retrieve.py:52-59`, `federated_search.py:739-759`). It falls back to the legacy orchestrator. The harness measures what users get. When the DAG is fixed, the same harness compares the two through `strategy_id` A/B.
- **Tools are code-dispatched, not LLM function calls.** The orchestrator LLM emits JSON tasks that code dispatches by `TaskType`. "Correct tool with correct arguments" therefore means correct `TaskDefinition`s (type, query, sources, max_results) in the plan and in executed steps. The old `/api/v1/chat` endpoint with native function calling is not what the UI uses (`ChatPageNew.tsx:423,513`).
- **Cost estimate.** 100 cases × 5 repeats in thinking mode is roughly 2,000–3,000 LLM calls per full run. The retrieval-only harness costs nothing once the embedding cache is warm. Run nightly jobs only when the stack is idle.
- **Out of scope for v1, with reasons.**
  - Langfuse or Phoenix: a new service. Revisit for online evaluation of production traces.
  - Inspect AI and promptfoo: they duplicate the in-house runner and add a second config system.
  - DeepEval and RAGAS packages: their judges can be added later behind `JudgeHook` if the in-house judges underperform.
  - OpenAI Evals and graders: being retired.
- **Risks.**
  1. Markdown-vs-Docling text alignment may miss more spans than expected. Mitigation: normalized matching with an offset map, coverage reporting, and re-ingesting `rag_test_law` (cheap) if needed.
  2. The global profile switch disturbs concurrent users. Mitigation: the lock, and running on an idle stack or a dedicated eval container.
  3. Changing the citation format changes the UX. Mitigation: config flag, measured A/B before switching the default.
  4. Dataset authoring needs a domain reviewer's time. It is the critical path, and code alone cannot replace it.
- **Confidence score: 7/10** for one-pass execution of Phases 0–3. The main uncertainty is span alignment on legacy chunks. Phase 4 depends on human labeling time.
