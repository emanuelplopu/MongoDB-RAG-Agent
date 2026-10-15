# 20 · Synthetic Austrian Law-Firm Corpus

`backend/evaluation/synthetic_corpus/` generates a **fictional Austrian law firm (Kanzlei)** with complete, internally consistent case files and **gold questions** whose correct documents and text passages are known by construction. It is the test data for the golden evaluation harness (`.agents/plans/agent-retrieval-evaluation-harness.md`).

Real client files must never be used for shared tests. Complete Austrian case files are not published anywhere, so the generator builds them from public court decisions plus a structured fact ledger.

## Pipeline

| Stage | What happens | Model |
|---|---|---|
| `seeds` | Fetch recent decisions per area from the RIS OGD API as factual inspiration. Optionally invent new case seeds for areas with too few. | orchestrator (seeds only) |
| `plan` | Build a case ledger per seed. The ledger holds identities, proceedings, 40 to 70 atomic facts, events, and 20 or more planned documents, including cost items. Code then post-processes it. | orchestrator |
| `write` | Write every planned document in Austrian legal German. Code checks that every required value appears verbatim and retries with feedback if one is missing. | generator |
| `render` | Produce DOCX, PDF, EML, scanned JPG, TXT and optionally legacy DOC files in a firm folder tree, plus deliberate copies and draft versions. | none |
| `questions` | Generate gold questions. The model only phrases them; documents, quotes and must-include values come from code. Unanswerable and cross-case questions are added. | orchestrator |
| `validate` | Run consistency checks across ledger, files and questions. | none |

**Default models:** `gpt-6-astra` (orchestrator) and `gpt-6.1-sol` (generator), both through the OpenAI Responses API with structured JSON outputs. Neither accepts `temperature`; set `reasoning_effort` instead.

### What code guarantees, not the model

- **Names.** All names come from a seeded Faker `de_AT` factory.
  - Surnames are unique within a run.
  - Lawyer and judge surnames found in the RIS seed decisions are blocked.
  - Postcodes and towns are real pairs.
- **Case numbers (Geschäftszahlen)** follow court and register formats. A case number becomes known only from the court's or authority's first own document; initiating pleadings never contain it.
- **Display formats** are `EUR 1.234,56` and `12.03.2025`.
- **Cost notes** are computed by `fees.py` from date-effective RATG and GGG tables taken from RIS and BGBl: Einheitssatz, ERV surcharge, USt, court fees. The model only chooses posts, using the service mapping in `data/reference/austrian_procedure.json`.
- **Gold labels.**
  - Gold documents are the files the plan assigned a fact to.
  - Each quote is a text-quote selector (quote, prefix, suffix). When a value occurs several times, the occurrence whose line best matches the fact label is chosen.
  - Copies are listed as equivalent sources.
  - Draft-version values are listed as `must_not_include`.

## Usage

```powershell
# every setting with its default
uv run python -m backend.evaluation.synthetic_corpus init-config --out synth.json
# also available as: uv run quellexctl synth ...

uv run python -m backend.evaluation.synthetic_corpus areas list
uv run python -m backend.evaluation.synthetic_corpus cases list
uv run python -m backend.evaluation.synthetic_corpus run -c synth.json --run-name kanzlei_v1
uv run python -m backend.evaluation.synthetic_corpus status -c synth.json
```

- **Stage commands.** `seeds`, `plan`, `write`, `render`, `questions` and `validate` exist as separate commands.
- **Resumable.** Finished artifacts are reused. `--force` redoes a stage and invalidates all later stages for the affected cases.
- **Response cache.** Model responses are cached by prompt hash, so reruns cost nothing.
- **Overrides.** Any setting can be overridden on the command line: `--set llm.concurrency=8 --set 'run.areas=["mietrecht_mietzins_raeumung"]'`. Shortcuts exist: `--run-name`, `--output-root`, `--area`, `--case`, `--concurrency`, `--orchestrator-model`, `--generator-model`.

**API key.** Never put it in a config file. The config only names environment variables (`llm.api_key_env`, default `OPENAI_API_KEY` then `LLM_API_KEY`) and an optional env file (`llm.env_file`).

**Dependencies.** PDF rendering needs `fpdf2`, available in the `synthetic` dependency group: `uv sync --group synthetic`. Legacy `.doc` output needs LibreOffice (`soffice`); without it, the generator writes `.docx` and records a note (`corpus.doc_fallback`).

## Extending without code

- **New legal area.** Run `areas new <id> --name "..." --dir my_areas`, edit the JSON, and add the directory to `seeds.areas_dirs`. An area defines:
  - the procedure outline, courts and registers;
  - roles, which must include `mandant`;
  - document types, each with category, formats, guidance, `is_email` and `is_cost_note`;
  - question focus, RIS queries, Streitwert range and style notes.
- **New case seed.** Run `cases new --area <id> --id <id> --title "..." --description "..."`, or drop a JSON file into a `seeds.cases_dirs` directory. Per-role naming hints go in `role_hints` (gender, company word such as `Tischlerei`, legal form, postcode). If `corpus.cases_per_area` is larger than the number of seeds, the `seeds` stage invents new scenarios (`run.generate_missing_cases`).
- **Courts.** Edit `data/reference/courts.json`, or put a `courts.json` into an extra areas directory.
- **Prompts.** Templates are in `data/prompts/*.j2`. Copy one into `seeds.prompts_dir` to override it.
- **Fees.** Add a new `data/fee_tables/<law>_<valid_from>.json` when RATG or GGG amounts are valorized.

## Output (`<output_root>/<run_name>/`)

```
corpus/<Kanzlei>/Akten/<AKT-Jahr-Nr Mandant gegen Gegner>/01_Korrespondenz … 06_Intern, _Ablage, Posteingang
ledger/<case>.json            single source of truth per case (facts, documents, identities, cost notes)
manifest/<case>.json          every file with canonical text and the facts it contains
gold/questions.jsonl|json|yaml, gold/dataset.json
reports/validation.json, reports/usage.json, logs/llm_calls.jsonl
```

Paths can exceed 260 characters. The generator writes them with the Windows extended-length prefix. Some Windows tools need `LongPathsEnabled`; Docker bind mounts and Linux readers are unaffected.

## Ingesting a run

1. Mount `corpus/<Kanzlei>` into the backend and worker containers. Add a profile whose `documents_folders` points to the mount and whose database is new.
2. Ingest it.
3. Run the gold questions through the chat API.

`.eml` files are supported by ingestion from this change on (`src/ingestion/ingest.py`). Workers built before it skip them, so set `corpus.email_format_weights={"pdf":1}` for those.

## Benchmarking a running stack

Results go to `<run_dir>/bench/<bench-name>/`. Every command takes `--base-url`, `--profile`, `--database` and `--mongo-uri`. The defaults use `127.0.0.1`, not `localhost`: on Windows a new connection to `localhost` tries IPv6 first and waits about two seconds before it falls back, because the stack (`BIND_ADDRESS`) and Ollama bind IPv4 only.

```powershell
uv run python -m backend.evaluation.synthetic_corpus bench-ingest -c synth.json --bench-name bench1
uv run python -m backend.evaluation.synthetic_corpus bench-retrieval -c synth.json --bench-name bench1
uv run python -m backend.evaluation.synthetic_corpus bench-chat -c synth.json --bench-name bench1 --mode fast
uv run python -m backend.evaluation.synthetic_corpus bench-chat -c synth.json --bench-name bench1 --mode thinking
uv run python -m backend.evaluation.synthetic_corpus bench-telemetry -c synth.json --bench-name bench1 --run chat_thinking
uv run python -m backend.evaluation.synthetic_corpus bench-query-rewrite -c synth.json --bench-name bench1
uv run python -m backend.evaluation.synthetic_corpus bench-rescore -c synth.json --bench-name bench1
uv run python -m backend.evaluation.synthetic_corpus bench-report -c synth.json --bench-name bench1
uv run python -m backend.evaluation.synthetic_corpus bench-report -c synth.json --bench-name bench2 --baseline bench1
uv run python -m backend.evaluation.synthetic_corpus bench-ab -c synth.json --a bench2 --b bench3
```

### `bench-ingest`

- Starts `POST /api/v1/ingestion/start` for the profile, polls the job, and samples the GPU, the host and the containers every 5 s (`--sample-interval`) into `resources_ingest.jsonl`; renders `telemetry_ingest.html` at the end (see `bench-telemetry`).
- Creates the search indexes and times how long they take to become READY.
- Parses the backend log into per-file stage timings: convert, chunk, embed and store.
- Measures **fact fidelity**: the share of planned values still present in the converted text and in the chunks, per file format.

**Safety guards:**
- A clean ingestion is refused unless the target database starts with `rag_synth`.
- The job is stopped immediately if the discovered file count does not match the corpus.

### `bench-retrieval`

- Sends every answerable gold question to `/search/{hybrid,semantic,text}`.
- Scores Hit@k, Recall@k and MRR at document level, counting copies as equivalent.
- Scores passage hit and passage recall: whether a gold quote occurs in the returned chunks.
- Requests are paced to the backend rate limit (30 per minute for search) and HTTP 429 responses are retried.

### `bench-chat`

- Sends every gold question through `/sessions/{id}/messages/stream`, the path the UI uses.
- Records time to first byte, per-phase durations and per-step tokens and tokens/s.
- Records tool calls with their queries and checks the arguments:
  - boolean or quoted syntax;
  - empty queries and malformed ones (leaked JSON, unbalanced quotes);
  - names from the question spelled differently in a query (`Erhart->Ehrhart`).
- Records each orchestrator step's output and flags JSON parse failures and cuts by the repetition guard.
- Checks whether the agent's sources contain the gold document and whether its retrieved chunks contain the gold passage.
- Scores the answer:
  - required values, compared format-independently, so "3. März 2025" equals "03.03.2025" and "1.700 €" equals "EUR 1.700,00";
  - draft values that must not appear;
  - nuggets, for questions without required values: a nugget counts when its dates and amounts and at least 60 % of its content words (compared by a five-letter prefix) appear in the answer;
  - **answer correct** (`answer_ok`): all required values, or at least 60 % of the nuggets, and no draft value;
  - abstention, which is correct on unanswerable questions and false on answerable ones;
  - citations.
- Assigns each question the **first stage that lost the answer**, in pipeline order: plan (no usable query), retrieval (gold document or gold passage missing), synthesis (cut off, abstained despite evidence, or incomplete).
- Resumable per question. Benchmark sessions are deleted afterwards unless `--keep-sessions` is set.
- Records telemetry so one long run is enough to find problems; the summary lists everything that needs a look under `anomalies`:
  - **per LLM call** (agent trace): prompt and output tokens, estimated hidden reasoning tokens, whether the output hit `max_tokens`, JSON parse failures and retries, loop trimming, the provider options actually sent;
  - **fallback paths** (`degradations` in the trace): default tasks, repaired or empty queries, evaluate fallbacks, empty synthesis;
  - **Ollama server log**, sliced per question by byte offset: request status and server time, prefill and decode tokens and speed, context size and use, prompts truncated to fit, model loads and evictions, aborted requests, errors; plus which models are loaded and whether part of one runs on the CPU;
  - **backend log** per question: warnings, errors, tracebacks, exception types;
  - **robustness**: a health check before every question, per-question error capture (the run continues and stops after 5 consecutive failures), stream anomalies, container restarts and OOM kills, Ollama restarts or version changes;
  - **resources** every 5 s (`--sample-interval`) into `resources_<run>.jsonl`, with the machine facts in `resources_<run>.meta.json`: GPU utilisation (SMs and memory controller), VRAM used and total, power and power limit, temperature and the headroom to the thermal slowdown limit, SM and memory clock, fan, P-state, the active clock-event reasons and the driver's cumulative throttle counters; host CPU (total and per core), RAM, swap, disk and network traffic, the ACPI thermal zone where Windows exposes one, RSS and CPU of the Ollama processes and of the Docker VM; per container CPU, memory, block and network I/O; Ollama's loaded models; the backend health round trip. The summary adds drift over the run, seconds under each clock limit, thermal slowdown time and host peaks, and `anomalies` flags thermal slowdowns, a GPU within 3 °C of its limit, RAM over 90 %, swap use and failing probes;
  - **MongoDB** operations slower than 100 ms (profiling level 1 on the benchmark database during the run);
  - **stability**: latency of the first vs the last third of the run, outliers, slowest phase of the slowest questions;
  - **isolation**: share of an answer's sources that come from another case.
- `--concurrency N` runs the questions N at a time as a load probe (results in `chat_<mode>_c<N>.jsonl`). It shows queueing in Ollama, errors under load, and cross-talk between sessions (sources from another case).

### `bench-query-rewrite`

Sends the queries the planner wrote in a thinking run to hybrid search, as written and with quotes and AND/OR/NOT removed, and compares them with searching the plain question. Questions without any usable planner query count as misses.

### `bench-ab`

Compares two runs question by question (paired A/B): correct answers in each arm, the questions gained and lost with an exact two-sided sign test, the same restricted to questions whose retrieval outcome was identical in both arms (to separate answer writing from retrieval), latency of the whole answer and of the answer-writing phase, answer length, citations, hidden reasoning tokens and budget hits. Writes `ab_<a>_vs_<b>.json` into run B. A difference reaches p < 0.05 only with a clear one-sided shift among the changed questions, for example 6 gained and none lost, or 8 gained and 1 lost.

### `bench-rescore`

Rescores stored chat results with the current scorer and rewrites the summaries. It makes no LLM calls. `--log-since` (and `--log-until`) also counts orchestrator LLM calls, JSON parse failures, repetition-guard cuts and evaluate decisions in the backend log for that window.

### `bench-telemetry` and the HTML telemetry page

`bench-chat` and `bench-ingest` end by rendering `telemetry_<run>.html` next to their results, `bench-report` renders a page for every `resources_*.jsonl` of a bench and links them from `REPORT.md`, and `bench-telemetry --run chat_thinking` renders one on demand, also while a run is still going (the sampler appends to the files the page reads). The page is one self-contained HTML file (inline SVG, no external resources, light and dark) and shows on one time axis:

- stat tiles: run time, questions correct, GPU temperature max with the headroom to the slowdown limit, time at the power cap, thermal slowdown time, VRAM peak, GPU power, SM clock, host RAM and CPU peaks, Ollama decode and prefill speed;
- the machine: GPU model, driver, VRAM, power limit, max clocks, PCIe link; host CPU, cores, RAM, Docker engine CPUs and memory; Ollama version, models and context lengths;
- everything the summary lists under `anomalies`, plus thermal limits, the driver's throttle counters, swap and RAM pressure;
- a question strip (outcome per question, agent phase below it) over line charts of GPU temperature with the slowdown limit, utilisation, power with its limit, SM clock with the clock-limit reasons shaded, VRAM with Ollama's share, fan, host memory (host, Docker VM, Ollama processes, swap), host CPU, disk and network rates, container CPU, memory and block reads, the backend health round trip, and a per-core CPU heatmap;
- per question: answer time, Ollama decode and prefill speed as columns, and a table with the GPU temperature, utilisation, power, power-cap share and VRAM while each question ran;
- events: model loads and unloads, failed health checks, container restarts, probe errors.

Every chart has a crosshair tooltip that lists all series, arrow-key navigation and a per-minute table view. Series files written before the sampler recorded host metrics render too, with the charts their data supports.

## Agent fixes found by the benchmark

The first thinking-mode run (bench1, gemma4:12b) answered 17% correctly against 54% in fast mode. The causes, all fixed in `backend/agent/`:

| Problem | Effect | Fix |
|---|---|---|
| The repetition guard flagged any 10-character substring seen 6 times, which indented JSON always contains, and cut the output to 200 characters | 44% of orchestrator outputs unparsable, 13 answers cut off | Loop detection by repeated lines, a repeating tail block or compression ratio; trims at the loop start; not applied to JSON |
| gemma4 reasons before answering unless `think=false`; LiteLLM drops the reasoning but it counts against `max_tokens` | Each phase 5 to 10 times slower, JSON cut off before it started | `think` off by default (`AGENT_OLLAMA_THINK_PHASES`), JSON mode for JSON phases |
| Strategies default `search_queries.primary` to "" when the analysis cannot be parsed | 24% of questions searched with an empty query | Empty or malformed queries are repaired or replaced by the user's question |
| Unparsable evaluation defaulted to "sufficient, 0.5" | The refinement loop never ran | Decide from the results found; retry unparsable JSON once |
| The planner rewrote queries: misspelt names, English terms, leaked JSON | Gold document in the top 10: 54% for planner queries vs 95% for the question | The user's question is always searched as well (`AGENT_ANCHOR_RAW_QUERY`) |
| Ollama loaded every model with the app's global 128k context | gemma4:e4b needed 21 GiB on a 16 GB GPU and evicted the 12b | `num_ctx` 32768 for agent calls |
| Every search task ran `count_documents({})` on the chunks collection | A full scan through all chunks and embeddings per search (100 ms on 777 chunks) | An existence check with `find_one` |

## Legal and licensing notes

- **Seeds.** RIS OGD data is CC BY 4.0, and court decisions are free works under § 7 Abs 1 UrhG. Each ledger lists the decisions it used (`ris_sources`).
- **RIS rules.** The client sends at most one request every 2 seconds, never in parallel, and caches everything. Bulk pulls belong at night or on weekends, announced to RIS.
- **Fictional content.** Generated documents are fictional. Firm, company and person names can coincide with real ones by chance. Do not publish a corpus without a name check.
- **Fee tables.** Verification status and sources are recorded in each table. A lawyer should review post choices; see `notes` in `data/reference/austrian_procedure.json`.
