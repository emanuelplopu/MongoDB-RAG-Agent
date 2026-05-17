# Quellex / RecallHub Strategy Operating System — Executive Roadmap

**Date:** 2026-05-17  
**Purpose:** Turn the current prompt-to-response pipeline, strategy engine, telemetry, profiler, and benchmark components into a flexible, schedulable experimentation and production-optimization layer.

---

## 1. What already exists in the uploaded blueprints

The uploaded blueprints already contain the important base components:

| Existing component | Current capability | Gap to close |
|---|---|---|
| Federated Agent | `analyze -> plan -> execute -> evaluate -> synthesize` with orchestrator + workers | Too rigid, too slow for many task classes, insufficient strategy-level observability |
| Agent Strategies | Registry, domains, prompts, metrics, A/B comparison | Strategies are still mostly prompt/config wrappers, not full executable pipeline patterns |
| Prompt Management | Versioned prompt templates and compare/test endpoints | No full prompt-program lifecycle across complete pipeline runs |
| Telemetry System | JSONL traces, LLM calls, search ops, phase metrics, PII modes | Needs experiment IDs, strategy spec hashes, evidence cards, judge results, replay metadata |
| Telemetry Viewer | Standalone Electron viewer for local JSONL and remote API | Needs strategy/experiment comparison views and regression dashboards |
| Embedding Benchmark | Provider/model benchmarking | Needs query-answer benchmark and retrieval/citation benchmark integration |
| Hardware Profiler | Static hardware recommendation | Needs runtime strategy profiler: real tokens/s, model load times, memory pressure, GPU/CPU utilization |
| Offline Deployment | Tenant-specific install and backup | Needs headless strategy CLI and scheduler that can run without frontend containers |

---

## 2. Target state

Build a **Strategy Operating System** on top of the current app.

It should allow the team to define, test, compare, schedule, profile, and promote many prompt-solving patterns, for example:

- fast RAG answer
- deep multi-step legal analysis
- matter-scoped legal drafting
- evidence-card synthesis
- source-audit mode
- contradiction matrix mode
- map-reduce summarization
- retrieval-only answer
- ensemble answer with judge selection
- local-only privacy mode
- hybrid cloud/local speed mode
- minimal-context mode
- high-citation-precision mode

The user-facing system should stay simple, but internally every response should be a measurable execution of a strategy spec.

---

## 3. Proposed blueprint set

| File | Main topic | Agent team |
|---|---|---|
| `01-BUSINESS_LAYER_AND_PROMPT_PIPELINE.md` | Business-layer routing and prompt-to-response architecture | Product + backend + prompt engineers |
| `02-STRATEGY_ENGINE_V2.md` | Config-driven strategy DAG, pattern execution, answer contracts | Backend agent team |
| `03-STRATEGY_PATTERN_LIBRARY.md` | Initial strategy patterns and variants to implement/test | Prompt + RAG team |
| `04-EVALUATION_TEST_CORPUS_AND_JUDGING.md` | Test cases, rubrics, LLM judges, deterministic validators | QA + backend |
| `05-OVERNIGHT_EXPLORATION_SCHEDULER.md` | Scheduled strategy exploration, bandit/grid search, promotion logic | Backend + ops |
| `06-HEADLESS_CLI_AND_BACKEND_ONLY_RUNNER.md` | CLI and no-frontend execution modes | DevOps + backend |
| `07-TELEMETRY_PROFILING_AND_ANALYTICS.md` | Strategy telemetry extensions, replay, dashboards | Telemetry + frontend/tools |
| `08-RUNTIME_PERFORMANCE_AND_RESOURCE_ROUTING.md` | Local LLM speed, hardware-aware model routing, budgets | Backend + infra |
| `09-DATABASE_AND_API_CONTRACTS.md` | Collections, schemas, endpoints, queue contracts | Backend |
| `10-IMPLEMENTATION_AGENT_PACK.md` | Concrete tickets/prompts for LLM coding agents | Delivery lead |
| `11-SECURITY_PRIVACY_AND_TENANT_GOVERNANCE.md` | Privacy, legal matter boundaries, PII, offline constraints | Security + product |
| `12-MVP_SEQUENCE_AND_ACCEPTANCE_TESTS.md` | Implementation order and release gates | Delivery lead + QA |

---

## 4. Highest-impact changes

### P0 — immediate structural changes

1. **Make strategy execution config-driven** instead of hardcoding strategy behavior in Python subclasses only.
2. **Add answer contracts** so each strategy defines the final response shape, citation standard, source policy, and unsupported-claim behavior.
3. **Add evidence cards** as the central interface between retrieval and synthesis.
4. **Add experiment datasets and strategy profiling runs** as first-class database entities.
5. **Add headless CLI** so experiments and nightly profiling run without frontend containers.
6. **Extend telemetry** to include strategy spec hash, experiment ID, dataset/test-case ID, node timings, judge results, and resource snapshots.
7. **Add scheduler/explorer** for overnight strategy search.
8. **Add promotion workflow** so a winning strategy can become default only after passing quality, latency, citation, and privacy gates.

### P1 — quality and speed improvements

1. Add matter/profile source scoping and cross-matter restrictions.
2. Add version-family deduplication for documents like `v1`, `v2`, `v3`.
3. Add boilerplate/signature/disclaimer suppression.
4. Add retrieval/citation validators.
5. Add model role routing: cheap/small model for classification, bigger model only for high-value synthesis.
6. Add runtime model/resource profiler to complement the static hardware profiler.

### P2 — product differentiators

1. Strategy leaderboard per tenant/domain/use case.
2. Nightly regression report exported as Markdown/HTML/PDF.
3. Strategy marketplace/config library for legal, finance, project-management, sales, engineering, and operations tasks.
4. Semi-automatic strategy mutation and bandit optimization.
5. Trace replay from telemetry records for deterministic bug reproduction.

---

## 5. Reference architecture

```text
User Prompt
  -> Business Context Resolver
       - tenant: quellex | recallhub
       - profile / matter / source policy
       - user role / privacy mode
       - business capability classification
  -> Strategy Selector
       - explicit strategy OR auto-selected strategy OR experiment candidate
       - latency / privacy / quality budget
  -> Strategy Runner
       - executes config-driven DAG nodes
       - retrieval, rerank, evidence cards, synthesis, validation
  -> Quality Gate
       - answer-contract adherence
       - citation coverage
       - unsupported claim detector
       - latency / resource budget
  -> Response
       - final answer
       - source map
       - operational trace summary
  -> Telemetry / Experiment Result
       - full trace, metrics, judge scores, replay metadata
```

---

## 6. Implementation principle

Do not build one more monolithic prompt.

Build a system where different prompt, retrieval, context, model, validation, and answer-shaping patterns can be composed and measured.

The core product improvement is not a better single strategy. It is the ability to continuously discover which strategy is best for which tenant, domain, profile, hardware, and prompt class.
