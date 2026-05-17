# Blueprint 12 — MVP Sequence and Acceptance Tests

## 1. Objective

Define the implementation order for the Strategy Operating System so the team can deliver value incrementally without destabilizing the existing app.

---

## 2. MVP phases

## Phase 0 — Safety and compatibility baseline

### Build

- Add migration scaffolding.
- Add strategy spec models but do not yet route production chat through them.
- Add adapter for existing strategies as legacy specs.
- Add protected telemetry fields for strategy ID/version/spec hash.

### Acceptance

- Existing chat still works.
- Existing strategies list still works.
- Legacy strategy emits spec hash.
- No frontend changes required.

---

## Phase 1 — Business context and answer contracts

### Build

- `BusinessContextResolver`
- `SourcePolicy`
- `AnswerContract`
- deterministic capability rules
- initial Quellex legal contracts
- initial RecallHub business contracts

### Acceptance

- The Ergänzungsfragen prompt resolves to `legal_hearing_questions`.
- Ambiguity is represented explicitly.
- Source policy defaults to no cross-matter/no web for Quellex legal.
- Answer contract is attached to response envelope.

---

## Phase 2 — Fast evidence strategy

### Build

- StrategySpec YAML loading
- StrategyRunner minimal DAG
- nodes: normalize, retrieve, dedupe/filter, evidence_cards, synthesize, validate, telemetry
- implement `legal_hearing_questions__fast_evidence__v1`

### Acceptance

- Run one prompt through strategy by API.
- Produces legal hearing question table.
- Uses evidence cards.
- Does not run full analyze/plan/evaluate chain.
- Latency is materially lower than legacy orchestrator on local LLM.

---

## Phase 3 — Evaluation runner and seed datasets

### Build

- dataset YAML loader
- deterministic validators
- evaluation result persistence
- simple leaderboard
- `quellex_legal_core_v1` and `recallhub_business_core_v1`

### Acceptance

- CLI/API can run dataset against two strategies.
- Results persisted.
- Leaderboard generated.
- Fatal source-scope violation fails score.

---

## Phase 4 — CLI

### Build

- `quellexctl strategy list/get/validate`
- `quellexctl run prompt`
- `quellexctl experiment run`
- `quellexctl dataset list/validate`

### Acceptance

- Commands work with backend-only containers.
- JSON and Markdown output supported.
- Non-zero exit codes on failures.

---

## Phase 5 — Scheduler and reports

### Build

- schedule collection/API
- scheduler worker
- run-now
- nightly report generation
- regression mode

### Acceptance

- Schedule can be created and run via CLI.
- No frontend needed.
- Report generated.
- Resource limits checked.

---

## Phase 6 — Exploration and runtime profiler

### Build

- candidate generator
- grid exploration
- runtime model profiler
- resource snapshots
- promotion candidates

### Acceptance

- Overnight exploration creates candidate strategies.
- Runtime profiler measures local models.
- Report recommends winner and promotion candidates.
- No automatic promotion unless configured.

---

## 3. Definition of done for each phase

Each phase must include:

- backend code
- CLI/API coverage where relevant
- tests
- telemetry fields
- documentation
- no regression to existing chat
- no frontend dependency for core functionality

---

## 4. Critical acceptance test: local legal prompt speed

Test prompt:

```text
ich suche ergänzungsfragen von einen immobilienprozess
```

Baseline problem:

```text
legacy local orchestrator can take many minutes
```

Target behavior with fast evidence strategy:

```text
- resolves to legal_hearing_questions
- searches active profile/matter
- builds evidence cards
- synthesizes table
- validates citations/contract
- avoids full 26B planning unless needed
```

Target performance:

| Environment | First target | Stretch target |
|---|---:|---:|
| local CPU/RAM-only | < 90 sec | < 45 sec |
| local GPU-assisted | < 45 sec | < 20 sec |
| cloud/hybrid | < 20 sec | < 10 sec |

Quality target:

- no unrelated matter leakage
- citation coverage > 85%
- answer contract pass
- judge quality > 0.80 on legal hearing scoring profile

---

## 5. Regression gates

Before any strategy becomes default:

- pass `quellex_legal_core_v1`
- pass `retrieval_regression_v1`
- pass privacy/source-scope validators
- p95 latency under configured hard limit
- no fatal failures
- manual approval for legal/default strategies

---

## 6. Minimal first implementation backlog

| Priority | Ticket | Phase |
|---|---|---|
| P0 | Add StrategySpec model and legacy adapter | 0 |
| P0 | Add BusinessContextResolver | 1 |
| P0 | Add AnswerContract and SourcePolicy | 1 |
| P0 | Add StrategyRunner minimal DAG | 2 |
| P0 | Add evidence cards | 2 |
| P0 | Add legal hearing questions strategy | 2 |
| P0 | Add telemetry strategy fields | 2 |
| P1 | Add evaluation dataset and deterministic validators | 3 |
| P1 | Add CLI run prompt/experiment | 4 |
| P1 | Add scheduler run-now and report | 5 |
| P2 | Add candidate generator and runtime profiler | 6 |

---

## 7. Final success criteria

The app can run this from a terminal with only backend services available:

```bash
quellexctl experiment run \
  --tenant quellex \
  --profile rag_test_law \
  --dataset quellex_legal_core_v1 \
  --strategies legacy_legal_orchestrator_v1,legal_hearing_questions__fast_evidence__v1 \
  --report reports/legal_strategy_comparison.md
```

And it produces:

- response quality scores
- latency and token metrics
- retrieval/citation metrics
- source-scope failures if any
- recommendation whether the fast evidence strategy beats the legacy orchestrator
