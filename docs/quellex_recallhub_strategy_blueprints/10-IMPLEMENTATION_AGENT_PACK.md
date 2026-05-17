# Blueprint 10 — Implementation Agent Pack

This file contains task prompts that can be handed to LLM coding agents.

---

## Agent 1 — Business Context Resolver

### Mission

Implement the business-layer resolver that classifies user prompts into capabilities, source policies, answer contracts, and strategy candidates.

### Files to create

```text
backend/models/business_context.py
backend/services/business_context_resolver.py
backend/config/capabilities/quellex_legal.yaml
backend/config/capabilities/recallhub_business.yaml
backend/config/answer_contracts/legal_hearing_questions_table.yaml
backend/config/answer_contracts/business_insight_report.yaml
```

### Files to modify

```text
backend/agent/coordinator.py
backend/models/telemetry.py
```

### Requirements

1. Add `BusinessContext`, `SourcePolicy`, `AnswerContract`, `QualityBudget`, and `PrivacyPolicy` models.
2. Implement deterministic capability detection rules.
3. Add optional small-model classifier only when deterministic confidence is low.
4. For Quellex legal/matter prompts, default to profile/matter source scope and no web.
5. Return answer contract and strategy candidates.
6. Include ambiguity resolution and `must_state_in_answer` flag.

### Acceptance tests

- Prompt `ich suche ergänzungsfragen von einen immobilienprozess` resolves to `legal_hearing_questions` when active legal profile/matter exists.
- Prompt does not resolve to generic Immobilienkauf if legal matter evidence exists.
- Source policy has `allow_cross_matter=false`, `allow_web=false`, `require_source_spans=true` for legal matter prompts.

---

## Agent 2 — StrategySpec and Strategy Runner

### Mission

Extend the current strategy engine to support config-driven StrategySpecs and DAG node execution.

### Files to create

```text
backend/models/strategy_spec.py
backend/agent/strategy_runner.py
backend/agent/nodes/base.py
backend/agent/nodes/normalize.py
backend/agent/nodes/retrieve.py
backend/agent/nodes/evidence_cards.py
backend/agent/nodes/synthesize.py
backend/agent/nodes/validate.py
backend/config/strategies/legal/legal_hearing_questions__fast_evidence__v1.yaml
```

### Files to modify

```text
backend/agent/strategies/base.py
backend/agent/strategies/registry.py
backend/routers/strategies.py
backend/agent/coordinator.py
```

### Requirements

1. Load strategy specs from YAML and DB.
2. Validate graph nodes, model roles, prompts, budgets, and policies.
3. Execute DAG nodes with shared `StrategyRunState`.
4. Wrap legacy orchestrator as `legacy_orchestrator_pipeline` node.
5. Return `AgentResponseEnvelope`.
6. Emit telemetry with strategy ID/version/spec hash.

### Acceptance tests

- YAML strategy validates.
- CLI/API can run strategy by ID.
- Legacy strategy still works.
- Node metrics are recorded.

---

## Agent 3 — Evidence Cards and Source Hygiene

### Mission

Implement evidence cards and source hygiene filters.

### Files to create

```text
backend/models/evidence.py
backend/services/evidence_card_service.py
backend/services/source_hygiene_service.py
backend/services/version_family_service.py
```

### Requirements

1. Convert retrieved chunks into evidence cards.
2. Support legal question cards, contradiction cards, fact cards, business insight cards.
3. Deduplicate duplicate document versions.
4. Prefer latest version if metadata indicates v1/v2/v3/date.
5. Filter boilerplate: email signatures, confidentiality footers, repeated disclaimers.
6. Preserve source ID, chunk ID, span ID where available.

### Acceptance tests

- Duplicate `Ergänzungsfragen Covic_v2/v3` groups are recognized.
- Boilerplate-only Outlook/email footer chunks are filtered or downweighted.
- Evidence cards contain enough information for synthesis without raw 20k context.

---

## Agent 4 — Evaluation Runner and Judge

### Mission

Implement test datasets, deterministic validators, LLM judging, and composite scoring.

### Files to create

```text
backend/models/evaluation.py
backend/evaluation/runner.py
backend/evaluation/validators.py
backend/evaluation/judges.py
backend/evaluation/scoring.py
backend/evaluation/datasets/quellex_legal_core_v1.yaml
backend/evaluation/datasets/recallhub_business_core_v1.yaml
backend/routers/evaluation.py
```

### Requirements

1. Load datasets from YAML and DB.
2. Run dataset against multiple strategies.
3. Implement deterministic validators for answer contract, citations, source scope, forbidden phrases, required topics.
4. Implement optional LLM judge with blind pairwise and reference-grounded modes.
5. Persist evaluation results.
6. Compute leaderboard.

### Acceptance tests

- Dataset run produces one result per strategy/test-case pair.
- Deterministic validators run with judge disabled.
- Fatal failures force composite score to zero or fail state.
- Leaderboard ranks strategies by capability and dataset.

---

## Agent 5 — Overnight Scheduler

### Mission

Implement schedulable strategy experiments and exploration.

### Files to create

```text
backend/models/strategy_schedule.py
backend/services/strategy_scheduler_service.py
backend/workers/strategy_scheduler_worker.py
backend/evaluation/candidate_generator.py
backend/routers/strategy_schedules.py
```

### Requirements

1. Create/list/update/delete schedules.
2. Support cron and allowed time windows with timezone.
3. Generate experiment jobs.
4. Support regression, exploration, profiling, smoke modes.
5. Respect resource limits and pause conditions.
6. Generate Markdown/JSON reports.
7. Mark promotion candidates, but do not auto-promote unless explicitly configured.

### Acceptance tests

- Schedule can be run manually via API/CLI.
- Scheduler can run without frontend.
- Exploration generates candidate strategy specs and validates them.
- Report is generated after run.

---

## Agent 6 — Headless CLI

### Mission

Implement `quellexctl` CLI for strategy, dataset, experiment, schedule, telemetry, and profiling operations.

### Files to create

```text
tools/quellexctl/pyproject.toml
tools/quellexctl/quellexctl/cli.py
tools/quellexctl/quellexctl/client.py
tools/quellexctl/quellexctl/commands/strategy.py
tools/quellexctl/quellexctl/commands/dataset.py
tools/quellexctl/quellexctl/commands/experiment.py
tools/quellexctl/quellexctl/commands/schedule.py
tools/quellexctl/quellexctl/commands/telemetry.py
tools/quellexctl/quellexctl/commands/profiler.py
```

### Requirements

1. Support API mode and direct mode if feasible.
2. Provide commands listed in Blueprint 06.
3. Support JSON, YAML, Markdown, and table outputs.
4. Return non-zero exit codes on errors.
5. Work inside backend container and on host dev machine.

### Acceptance tests

- `quellexctl strategy list` works.
- `quellexctl run prompt ...` works.
- `quellexctl experiment run ...` works.
- `quellexctl schedule run-now ...` works.
- `quellexctl telemetry replay ...` works.

---

## Agent 7 — Telemetry and Analytics Extensions

### Mission

Extend telemetry records and analytics/reporting.

### Files to modify

```text
backend/models/telemetry.py
backend/services/telemetry_service.py
backend/agent/coordinator.py
backend/agent/strategy_runner.py
```

### Files to create

```text
backend/services/strategy_report_service.py
backend/routers/strategy_analytics.py
```

### Requirements

1. Add strategy/experiment/dataset fields to telemetry.
2. Add node metrics, evidence cards, validation, judge, resource snapshots.
3. Add analytics endpoints.
4. Generate Markdown and JSON reports.
5. Keep PII pseudonymization behavior.

### Acceptance tests

- Strategy run telemetry includes strategy spec hash and node timings.
- Protected telemetry redacts PII in new fields.
- Analytics endpoint returns leaderboard and latency distribution.

---

## Agent 8 — Runtime Profiler and Resource Router

### Mission

Implement runtime model/strategy profiler and resource-aware strategy selection.

### Files to create

```text
backend/services/runtime_profiler.py
backend/services/resource_monitor.py
backend/workers/runtime_profiler_worker.py
backend/routers/runtime_profiler.py
backend/services/adaptive_strategy_selector.py
```

### Requirements

1. Measure model latency/tokens/sec at multiple context sizes.
2. Capture CPU/RAM/GPU/VRAM snapshots where available.
3. Store runtime model profiles.
4. Make StrategySelector able to use runtime profiles.
5. Add model prewarm command/API.

### Acceptance tests

- `model-test` stores results.
- Strategy selection changes when a model is too slow for a latency budget.
- Overnight profiling can run headlessly.

---

## Agent 9 — Database/API Migrations

### Mission

Add collections, indexes, and routers required by the new system.

### Files to create

```text
backend/migrations/strategy_os_migration.py
backend/routers/strategy_specs.py
backend/routers/strategy_runs.py
backend/routers/evaluation.py
backend/routers/strategy_schedules.py
backend/routers/runtime_profiler.py
```

### Requirements

1. Create collections and indexes from Blueprint 09.
2. Add routers to FastAPI app.
3. Add auth requirements: admin for schedules/promotions, user for run prompt if allowed.
4. Ensure tenant/profile access controls are applied.

### Acceptance tests

- Migration is idempotent.
- API OpenAPI schema includes new endpoints.
- Unauthorized user cannot run cross-profile tests.

---

## Agent 10 — Test Harness

### Mission

Add tests for the full Strategy OS.

### Required tests

```text
tests/strategy_os/test_business_context_resolver.py
tests/strategy_os/test_strategy_spec_validation.py
tests/strategy_os/test_strategy_runner_fast_evidence.py
tests/strategy_os/test_evidence_cards.py
tests/strategy_os/test_evaluation_runner.py
tests/strategy_os/test_scheduler.py
tests/strategy_os/test_cli_smoke.py
tests/strategy_os/test_telemetry_extensions.py
```

### Acceptance

- Tests run in CI without frontend.
- Mock LLM and mock search are available for deterministic tests.
- At least one integration test runs against local MongoDB.
