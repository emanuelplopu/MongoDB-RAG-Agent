# Strategy OS — Overview

Last reviewed: 2026-05-18

The **Strategy OS** is the config-driven prompt-to-response pipeline that replaced the hard-coded orchestrator pipeline. It runs a typed DAG of nodes against a typed `StrategyRunState`, enforces source policies and budgets, persists every run for evaluation and observability, and exposes an exploration system that schedules nightly experiments to find better strategy specs.

This blueprint is the entry point. Each layer has its own detailed blueprint:

| Blueprint | Topic |
|---|---|
| [07-STRATEGY_OS_OVERVIEW.md](./07-STRATEGY_OS_OVERVIEW.md) | High-level architecture (this file) |
| [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md) | DAG runtime, the 18 node types, state model |
| [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md) | `StrategySpec` lifecycle, capabilities, answer contracts, source policies, promotion workflow |
| [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md) | 7-dimension scoring, judge calibration, contradiction detection, leaderboard |
| [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md) | Exploration modes, candidate generators, evolutionary mutator, scheduler daemon, pause/stop gates, nightly reports |

---

## 1. What Strategy OS Replaces

The original system had a single hard-coded pipeline inside `backend/agent/orchestrator.py`: analyze → plan → execute → evaluate → synthesize. Behavior changes (a different retrieval `top_k`, a reranker, a different prompt) required code changes and a redeploy. There was no way to A/B two pipelines, no machine-readable record of how an answer was produced, and no way to run an experiment overnight to find better settings.

Strategy OS makes the pipeline **declarative**:

- A `StrategySpec` is a YAML or Mongo document that fully describes the pipeline as a DAG of nodes plus typed policies (source, retrieval, evidence, validation), budgets, and an answer contract.
- A `StrategyRunner` compiles the spec to a level-synchronous DAG and executes it against a `StrategyRunState`, producing a `StrategyRunResult` plus a persisted `RunTraceDoc`.
- A `StrategySpecSelector` picks the right spec per request from `BusinessContext` (tenant, profile, detected capability, agent mode).
- An `EvaluationRunner` re-runs the same dataset against any number of strategies and produces a composite score per run.
- A `SchedulerDaemon` plus `StrategyExperimentRunner` runs candidate generators (grid, regression, smoke, bandit, evolutionary) overnight against resource gates and pause/stop conditions, and aggregates a `NightlyReport`.

The legacy pipeline is still callable as a single node — `legacy_orchestrator_pipeline` — so any spec can opt into the old behavior end-to-end without rewriting it.

---

## 2. Architectural Layers

Strategy OS is four loosely-coupled layers. Each layer has its own MongoDB collections and is testable in isolation.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 4 — Observability                                                 │
│   • RunTraceStore                  → strategy_runs (TTL)                │
│   • NightlyReportGenerator         → strategy_run_reports               │
│   • RuntimeProfiler                → runtime_model_profiles             │
│   • ChatActivityTracker            → runtime_signals                    │
│   • ResourceSnapshotCollector      → runtime_signals                    │
│   • TelemetryService               → JSONL (file-based)                 │
└─────────────────────────────────────────────────────────────────────────┘
            ▲ writes traces, profiles, reports, telemetry events
            │
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 3 — Exploration & Evaluation                                      │
│   • EvaluationRunner               → evaluation_results                 │
│   • CompositeScoreCalculator + JudgeCalibrator + ContradictionDetector  │
│   • ExplorationModes (grid/regression/smoke/bandit/evolutionary)        │
│   • CandidateGenerator + EvolutionaryMutator                            │
│   • StrategyExperimentRunner       → strategy_experiment_jobs           │
│   • SchedulerDaemon                → strategy_schedules, scheduler_reports │
│   • BanditStateStore               → strategy_bandit_state              │
│   • EvolutionStateStore            → strategy_evolution_state           │
│   • AdaptiveDecisionStore          → adaptive_selection_decisions       │
└─────────────────────────────────────────────────────────────────────────┘
            ▲ submits per-strategy + per-dataset runs to Layer 2
            │
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 2 — DAG Execution Runtime                                         │
│   • BusinessContextResolver        (capability + source policy + lang)  │
│   • StrategyRunner                 (level-sync DAG executor)            │
│   • GraphCompiler                  (topological levels, cycle check)    │
│   • ConditionEvaluator             (safe-AST edge conditions)           │
│   • NodeRegistry → 18 NodeExecutor implementations                      │
│   • ContextBudgeter, CheckpointManager, NodeLLMHelper                   │
└─────────────────────────────────────────────────────────────────────────┘
            ▲ loads the spec to run; writes the per-run trace
            │
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 1 — Spec & Governance                                             │
│   • SpecStore                      → strategy_specs                     │
│   • SpecLoader (YAML → StrategySpec)                                    │
│   • SpecSelector (capability + tenant + mode → spec)                    │
│   • PromotionManager + PromotionEvaluator                               │
│   • BusinessCapability, AnswerContract, SourcePolicy YAMLs              │
│        backend/config/strategy_specs/                                   │
│        backend/config/capabilities/                                     │
│        backend/config/answer_contracts/                                 │
│        backend/config/tenant_strategy_policies/                         │
└─────────────────────────────────────────────────────────────────────────┘
```

Each box maps to one Python package or service in the codebase. The arrows show data flow (specs flow up, traces flow down).

---

## 3. End-to-End Request Lifecycle

A normal user chat request flows through Strategy OS as follows:

1. **Inbound request** lands on a router in `backend/routers/` (`chat.py`, `sessions.py`, or `search.py`). The router resolves the authenticated user, profile key, and tenant.
2. **Business context resolution.** `BusinessContextResolver.resolve(query, profile_key, accessible_profiles, tenant_id)` returns a `BusinessContext` containing the detected `capability_id`, capability confidence, ambiguity state, language policy, and a `ResolvedSourcePolicy` that intersects tenant policy, profile policy, capability policy, and request overrides.
3. **Spec selection.** `StrategySpecSelector.select(capability_id, agent_mode, tenant_id, business_context)` picks the active `StrategySpec`. The default precedence is: explicit `strategy_id` override → tenant policy → capability default → global default. Decisions are cached in `adaptive_selection_decisions` with a TTL (default 30 days, configurable via `adaptive_decisions_ttl_days`).
4. **DAG compilation.** `compile_graph(spec.graph)` returns a `CompiledGraph` with topological levels. Cycles, unknown node references, and unreachable terminal nodes cause `GraphCompilationError`.
5. **DAG execution.** `StrategyRunner.run(spec, business_context, query, state_factory)` walks the topological levels concurrently. Each level's nodes are dispatched through `NodeRegistry.execute(node, state)` via `asyncio.gather`. Each node is wrapped with timing, an optional `timeout_ms`, and a retry policy. Budgets (`max_llm_calls`, `max_cost_usd`, `latency_hard_limit_ms`) are checked between levels; a breach halts the run with `halt_reason` set.
6. **State accumulation.** Each node returns a `NodeOutput` that the runner merges into `StrategyRunState` (append-only: `retrieved_chunks`, `evidence_cards`, `validation_results`, `node_outputs`). `StateViolationError` is raised if a node tries to mutate prior fields.
7. **Conditional edges.** After a level completes, `restricted_eval(edge.condition, state)` decides which downstream nodes to enqueue. The expression grammar is a safe-AST subset of Python (no dunders, no globals, no function calls beyond a small whitelist).
8. **Trace persistence.** The runner serializes a `RunTraceDoc` to `strategy_runs` (TTL controlled by `strategy_runs_ttl_days`, default 7 days). The trace contains every `NodeOutput`, the resolved policy, budget usage, and the final `SynthesisResult`.
9. **Response to caller.** `StrategyRunResult.state.synthesis_result.text` plus citations is returned to the router and streamed/serialized to the client.

Failures at any step fall back to the legacy orchestrator if `settings.strategy_os_enabled = False` or if no spec matches the capability. The legacy path remains accessible via the `legacy_orchestrator_pipeline` node, so a spec can mix old behavior with new validation/judging nodes.

---

## 4. Package Map

All Strategy OS Python code lives under `backend/`. The high-level package layout:

| Path | Role |
|---|---|
| `backend/agent/strategy/` | Layer 1 + 2: spec models, runner, DAG, state stores, exploration modes, business context resolver |
| `backend/agent/strategy/nodes/` | Node executor implementations — one file per node type |
| `backend/agent/strategy/prompts/templates.py` | LLM prompt factories for nodes that call into the language model |
| `backend/scheduler/` | Layer 3: scheduler daemon, circuit breaker, pause/stop evaluators, contention manager, Ollama residency, timezone resolver |
| `backend/evaluation/` | Layer 3: evaluation runner, composite scorer, judge calibrator, contradiction detector, test case manager |
| `backend/services/runtime_profiler.py` | Layer 4: per-model latency / token / cost profiles |
| `backend/services/resource_snapshot.py` | Layer 4: CPU/RAM/GPU pollers backing the pause evaluator |
| `backend/services/activity_tracker.py` | Layer 4: timestamps of recent user chat traffic (used to pause overnight runs) |
| `backend/services/activity_logger.py` | Layer 4: audit log writer |
| `backend/routers/strategies.py` | REST surface for legacy strategy A/B + feedback |
| `backend/routers/strategy_specs.py` | REST surface for `StrategySpec` CRUD + lifecycle |
| `backend/routers/evaluation.py` | REST surface for evaluation runs + leaderboard + test cases |
| `backend/routers/scheduler.py` | REST surface for schedules, pause/resume, reports |
| `backend/cli/strategy_commands.py` | `quellexctl strategy …` + `quellexctl prompt` |
| `backend/cli/eval_commands.py` | `quellexctl eval …` + `quellexctl experiment …` |
| `backend/cli/phase6_commands.py` | `quellexctl schedule …`, `quellexctl experiment …`, `quellexctl profiler …` |
| `backend/config/strategy_specs/*.yaml` | Bundled strategy specs, auto-seeded into Mongo at startup |
| `backend/config/capabilities/*.yaml` | Business capability definitions |
| `backend/config/answer_contracts/*.yaml` | Reusable answer contracts |
| `backend/config/tenant_strategy_policies/*.yaml` | Per-tenant policy overrides |

The legacy `backend/agent/strategies/` directory (note the plural) still holds the older `BaseStrategy` subclasses (`enhanced`, `legacy`, `software_dev`, `legal`, `hr`). They remain reachable through `backend/routers/strategies.py` and the legacy orchestrator path for backward compatibility, but new behavior lives in `backend/agent/strategy/` (singular).

---

## 5. MongoDB Collections Introduced by Strategy OS

| Collection | Owner | TTL | Purpose |
|---|---|---|---|
| `strategy_specs` | `SpecStore` (Layer 1) | — | Versioned strategy spec documents with optimistic locking |
| `strategy_runs` | `RunTraceStore` (Layer 2/4) | `strategy_runs_ttl_days` (default 7d) | Per-run DAG execution traces |
| `strategy_run_reports` | `NightlyReportGenerator` (Layer 4) | — | Daily aggregated run summaries |
| `strategy_experiment_jobs` | `ExperimentJobStore` (Layer 3) | — | Job lifecycle for scheduled/manual experiments |
| `strategy_schedules` | `MongoSchedulerStore` (Layer 3) | — | Cron-driven schedule definitions |
| `strategy_bandit_state` | `BanditStateStore` (Layer 3) | — | Multi-armed bandit arm statistics |
| `strategy_evolution_state` | `EvolutionStateStore` (Layer 3) | — | Generation/mutation history for evolutionary runs |
| `adaptive_selection_decisions` | `AdaptiveDecisionStore` (Layer 1) | `adaptive_decisions_ttl_days` (default 30d) | Cached spec-selection decisions |
| `runtime_model_profiles` | `RuntimeProfileStore` (Layer 4) | — | Per-model latency/token/cost profiles |
| `evaluation_results` | `EvaluationRunner` (Layer 3) | — | Per-run composite scores and dimension breakdowns |
| `scheduler_results` | `SchedulerDaemon` (Layer 3) | — | Per-schedule run outcomes |
| `scheduler_reports` | `SchedulerDaemon` (Layer 3) | — | Per-schedule aggregated summaries |
| `runtime_signals` | `ChatActivityTracker` + `ResourceSnapshotCollector` (Layer 4) | 24h | Shared chat-activity + resource-pressure signals across workers |
| `activity_log` | `ActivityLogger` (Layer 4) | — | Audit trail of user + system events |

All collections live in the tenant's active database. The same database hosts the original `documents`, `chunks`, `users`, `api_keys`, etc. — see [02-SYSTEM_BLUEPRINTS.md](./02-SYSTEM_BLUEPRINTS.md) and the master [01-SYSTEM_BLUEPRINT.md](../01-SYSTEM_BLUEPRINT.md) for the rest of the schema.

---

## 6. Configuration Surface

All Strategy OS settings live on `BackendSettings` in `backend/core/config.py`:

| Setting | Default | Effect |
|---|---|---|
| `strategy_os_enabled` | `True` | Master switch. When `False`, all requests fall back to the legacy orchestrator and the scheduler daemon is not started. |
| `strategy_spec_auto_seed` | `True` | On startup, seed `strategy_specs` from `backend/config/strategy_specs/*.yaml`. |
| `strategy_spec_dir` | `None` (uses `backend/config/strategy_specs`) | Override directory for YAML specs. |
| `model_roles_config` | `None` | Path to an optional `model_roles.yaml` override that maps logical roles (`synthesizer_deep`, `worker`, etc.) to concrete provider+model pairs. |
| `strategy_scheduler_auto_resume` | `True` | On startup, rebuild scheduler state from Mongo so schedules survive restarts. |
| `strategy_scheduler_tick_interval_seconds` | `60` | Scheduler daemon main-loop tick interval. |
| `adaptive_decisions_ttl_days` | `30` | TTL for cached spec selections in `adaptive_selection_decisions`. |
| `strategy_nightly_report_enabled` | `True` | Enable the daily nightly report generator inside the scheduler daemon. |
| `strategy_nightly_report_hour_local` | `3` | Local-time hour at which the nightly report runs. |
| `strategy_nightly_report_timezone` | `UTC` | IANA timezone for the nightly report cron (DST-safe via `ScheduleTimezoneResolver`). |
| `strategy_nightly_report_dir` | `data/reports/strategy/` | Output directory for the Markdown report. |
| `strategy_nightly_report_regression_threshold` | `0.05` | Composite-score drop that flags a regression. |
| `strategy_nightly_report_lookback_hours` | `24` | Lookback window the report aggregates over. |
| `strategy_runs_ttl_days` | `7` | TTL (days) for `strategy_runs` documents via Mongo TTL index on `created_at`. |

All Strategy OS settings can be set via `.env` or environment variables.

---

## 7. Integration Points

**Application startup (`backend/main.py` lifespan):**

1. Create the singleton `MongoSpecStore` from the active database handle. Inject it into the spec CRUD router, `StrategySpecSelector`, and `PromotionManager`.
2. Create the singleton `MongoRunTraceStore` and wire it into `StrategyRunner`. Ensure the TTL index on `created_at` exists (`strategy_runs_ttl_days`).
3. Build `NodeRegistry` via `create_default_registry(llm_helper=...)`. The optional `llm_helper` is a `NodeLLMHelper` wired against the active LLM provider config. When `None`, LLM-capable nodes fall back to rule-based behavior — useful for tests and for local-only setups without an LLM key.
4. If `strategy_spec_auto_seed` is `True`, call `seed_strategy_specs()` to upsert YAMLs from `settings.strategy_spec_dir` into `strategy_specs`.
5. Build the `MongoSchedulerStore` + `ExperimentJobStore` + `RuntimeProfileStore` + `BanditStateStore` + `EvolutionStateStore` + `AdaptiveDecisionStore`. Start the `SchedulerDaemon` if `strategy_os_enabled` and the daemon is not already running in another worker.
6. If `strategy_nightly_report_enabled` is `True`, register the nightly report cron inside the scheduler daemon.
7. Run the schema migration `migrate_strategy_os_collections()` to create the indexes documented in each store module.

**Request-time wiring:**

- Routers obtain the `StrategyRunner` and `StrategySpecSelector` from the FastAPI app state.
- The chat/search/session routers call `BusinessContextResolver.resolve(...)` first, then `select()`, then `run()`.
- If no spec matches or `strategy_os_enabled = False`, the router falls back to `FederatedAgent.orchestrate(...)` (legacy path).

**Background workers:**

- `backend/workers/ingestion_worker.py` is independent of Strategy OS — it pipes documents into `chunks` so Strategy OS can retrieve them. The two systems share no in-memory state; coordination is via MongoDB.

---

## 8. Read Next

- For DAG semantics, node contracts, and the 18 built-in node types → [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md)
- For spec authoring, capability mapping, answer contracts, source policies, and promotion → [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md)
- For evaluation scoring, judge calibration, contradiction detection → [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md)
- For exploration modes, candidate generators, scheduler daemon, and nightly reports → [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md)
- For the `quellexctl` CLI and REST endpoints → see the relevant sections in [02-SYSTEM_BLUEPRINTS.md](./02-SYSTEM_BLUEPRINTS.md) and per-feature blueprints above
