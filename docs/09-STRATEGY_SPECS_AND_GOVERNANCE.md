# Strategy OS — Specs, Capabilities & Governance

Last reviewed: 2026-05-18

This blueprint documents the **declarative side** of Strategy OS: how a `StrategySpec` is authored, where it lives, how the right one is selected for an incoming request, the policy layers that wrap around it (source, retrieval, validation, language), and the lifecycle that moves a spec from `draft` → `active` → `deprecated` → `archived` with proper guardrails.

For runtime behavior of a spec see [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md). For evaluation gates on promotion see [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md).

---

## 1. The Four Configuration Surfaces

Strategy OS keeps its declarative inputs in four sibling directories under `backend/config/`. All four are loaded at startup by the spec/capability/policy loaders and upserted into MongoDB when auto-seed is enabled.

| Directory | Type | Loaded into Mongo | Purpose |
|---|---|---|---|
| `backend/config/strategy_specs/` | `StrategySpec` YAML | `strategy_specs` collection | The DAG + budgets + per-spec policies. The unit of deployment. |
| `backend/config/capabilities/` | `BusinessCapability` YAML | In-memory + on disk | Business-domain capabilities (e.g. `legal_hearing_questions`, `business_summary`) that map a detected intent to a default spec + answer contract. |
| `backend/config/answer_contracts/` | `AnswerContract` YAML | In-memory + on disk | Reusable answer contracts (format, sections, table schemas, citation granularity). |
| `backend/config/tenant_strategy_policies/` | Tenant policy YAML | In-memory + on disk | Per-tenant defaults for `SourcePolicy`, `LanguagePolicy`, and the tenant-wide default capability. |

The pipeline at runtime intersects all four layers into a single `BusinessContext` plus a chosen `StrategySpec`.

---

## 2. `StrategySpec` — The Unit of Deployment

A `StrategySpec` is the top-level Pydantic model in `backend/agent/strategy/models.py`. The YAML form mirrors the model 1:1.

```yaml
strategy_id: "deep_orchestrated_v1"
version: "1.0.0"
display_name: "Deep Orchestrated Strategy v1"
description: "8-node DAG with query expansion, reranking, citation validation, and refinement"
tenant_scope: "shared"                # or a concrete tenant id
capability_id: null                   # optional binding to a capability
status: "active"                      # draft | active | candidate | deprecated | archived
spec_hash: "deep_orchestrated_v1_h1"  # used for cache invalidation + audit

graph:
  entry_node: "n_normalize"
  terminal_nodes: ["n_refine"]
  nodes: [ ... ]
  edges: [ ... ]

budgets:
  latency_target_ms: 15000
  latency_hard_limit_ms: 60000
  max_context_tokens: 32000
  max_output_tokens: 4000
  max_llm_calls: 30
  max_cost_usd: 0.50
  local_only: false

model_roles:
  synthesizer_deep: "orchestrator"
  worker: "worker"
  orchestrator: "orchestrator"

source_policy:        { ... }      # see §4
retrieval_policy:     { ... }
evidence_policy:      { ... }
validation_policy:    { ... }
answer_contract:      { ... }      # inline, or rely on capability default

tags: ["deep", "orchestrated", "validated"]
```

### Spec scoping

- `tenant_scope` is either a tenant id (`recallhub`, `quellex`) or `"shared"`. The selector restricts visible specs to `tenant_scope == request_tenant OR tenant_scope == "shared"`.
- `capability_id` (optional) binds the spec to a single capability. When `None`, the spec is eligible for any capability (or for explicit `strategy_id` overrides).

### Spec hash

`spec_hash` is a fingerprint of the DAG + budgets + policies. The runner records it on the `RunTraceDoc` so a trace can be tied back to the exact spec snapshot that produced it. When a YAML file is re-seeded with the same content the hash stays stable; any change triggers a new hash and creates a new version on the next upsert.

---

## 3. Capabilities & Capability Detection

A `BusinessCapability` (`backend/agent/strategy/models.py`) is a domain unit — what the user is trying to do.

```yaml
capability_id: legal_hearing_questions
display_name: Ergänzungsfragen / Beweisfragen
description: Generate supplementary questions for court hearings
tenant_scope: quellex
default_strategy_id: null               # optional, overrides selector default
default_answer_contract_id: legal_hearing_questions_table
detection_keywords: [ergänzungsfragen, beweisfragen, ...]
detection_patterns: ["(?i)fragen.*(?:gutachter|sachverständig|...)", ...]
default_source_policy: null             # optional per-capability override
scoring_profile: null                   # optional per-capability evaluation weights
```

### How capability detection works

`BusinessContextResolver.resolve(query, profile_key, accessible_profiles, tenant_id)` runs the following pipeline:

1. Load the tenant's `BusinessCapability` list (filtered by `tenant_scope == tenant_id OR == "shared"`).
2. For each capability, score the user query against `detection_keywords` (token-overlap with lemmatization where available) and `detection_patterns` (regex matches).
3. Combine scores → confidence per capability. Pick the top capability if its confidence exceeds the configured threshold (default `0.55`).
4. If two capabilities tie within a margin, populate `AmbiguityResolution` with both `alternative_interpretations` and request clarification at the UI level.
5. Resolve the answer contract: capability's `default_answer_contract_id` → tenant default → none.
6. Resolve the language policy (see §5) and the source policy (see §4).
7. Return a fully-populated `BusinessContext`.

The detected capability is the primary input to spec selection (§7).

---

## 4. Source Policy & Policy Intersection

`SourcePolicy` (per-spec) and tenant-level source policies are intersected into a single `ResolvedSourcePolicy` before any retrieval node runs. The intersection is the **strictest** of the layers.

### Layers (least → most authoritative)

1. **Spec source policy** — what the spec author intends.
2. **Tenant policy** — `backend/config/tenant_strategy_policies/<tenant>.yaml`. Cannot loosen the spec.
3. **Capability policy** — `BusinessCapability.default_source_policy`. Cannot loosen tenant.
4. **Request override** — the caller can explicitly tighten (e.g. drop `allow_cross_profile`). Cannot loosen any of the above.

The resolver's pseudocode:

```python
resolved = ResolvedSourcePolicy()
for layer in [spec.source_policy, tenant.source_policy, capability.source_policy, request.overrides]:
    resolved = intersect(resolved, layer)  # always picks the stricter value
resolved.resolved_from = [name for name in layers if applied]
```

### Knobs in `SourcePolicy`

| Field | Semantics |
|---|---|
| `allow_cross_profile` | May search profiles other than the caller's active one |
| `allow_cross_matter` | May search across matter ids (Quellex specific) |
| `allow_web` | May call out to the web tool / browser tool |
| `allow_personal` | May search personal cloud sources |
| `allow_cloud_private` | May search cloud-private sources |
| `web_requires_sanitization` | Web hits run through PII pseudonymizer before reaching the LLM |
| `prefer_latest_versions` | `dedupe_versions` node keeps the highest version per family |
| `exclude_boilerplate` | `boilerplate_filter` node strips boilerplate patterns |
| `require_source_spans` | All retrieved chunks must carry a span_start/span_end (otherwise omitted) |
| `primary_sources`, `secondary_sources`, `excluded_sources` | Whitelists/blacklists by source id |
| `allowed_profile_ids`, `allowed_matter_ids`, `excluded_matter_ids` | Profile-/matter-level scoping |

### Omitted-result reporting

When a chunk is dropped (forbidden source, low score, cross-matter, web-block, boilerplate, duplicate version, context budget, etc.) the dropping node appends an `OmittedResultEntry` with the `OmittedReason` enum to `state.metadata['omitted_results']`. The trace surfaces the count and reasons so operators can see *why* something didn't make it into the answer — not only what did.

---

## 5. Language Policy

`LanguagePolicy` lives on the tenant policy file (see `quellex.yaml` for the canonical example):

```yaml
language_policy:
  primary_languages: [de]
  supported_languages: [de, en]
  fallback_language: de
  detect_query_language: true
  enforce_response_language: true
  max_language_retry: 1
  terminology_preservation:
    de: [Sachverständiger, Gutachten, Beweisbeschluss, ...]
    en: [GDPR, API]
```

The resolver detects the query language, picks a response language consistent with the policy, and the `synthesize`/`refine` nodes inject a language instruction into the prompt. Domain terminology in `terminology_preservation` is passed verbatim — synthesizers are instructed not to translate it.

---

## 6. Answer Contracts

An `AnswerContract` is the structural promise of the response: format, sections, table schema, citation rules.

```yaml
format_id: legal_hearing_questions_table
version: 1
language: de
tone: professional
citation_granularity: chunk         # document | chunk | span
unsupported_claim_policy: flag      # flag | omit | allow
max_length_tokens: 4000
output_sections:
  - section_id: context
    title: Kontext
    required: false
    format: prose
  - section_id: questions_table
    title: Ergänzungsfragen
    required: true
    format: table
table_schema:
  columns:
    - name: priority
      column_type: enum
      enum_values: [hoch, mittel, niedrig]
      required: true
    # ... more columns
  row_source: evidence_cards
  sort_by: priority
  min_rows: 3
  max_rows: 20
required_fields: [questions_table]
```

Resolution order at runtime:

1. Spec-level inline `answer_contract` if present.
2. Capability's `default_answer_contract_id` → load from `backend/config/answer_contracts/`.
3. None — the synthesize node falls back to a free-form prose response.

The `validate_contract` node enforces that the synthesizer's output matches the contract — missing required sections, wrong column types, too few rows, or untyped enum values all add `ValidationIssue` entries with severity `error` or `fatal`. The `refine` node can be configured to react to these and re-synthesize.

---

## 7. Spec Selection: `StrategySpecSelector`

`StrategySpecSelector.select(capability_id, agent_mode, tenant_id, business_context)` is the routing point that decides which spec runs for a given request. Precedence (highest → lowest):

1. **Explicit request override.** `?strategy_id=...` on the API, or `--strategy <id>` on the CLI.
2. **Tenant policy default for the agent mode.** `tenant_strategy_policies/<tenant>.yaml` may pin a strategy id for `agent_mode in {fast, thinking, auto, deep}`.
3. **Capability default.** `BusinessCapability.default_strategy_id`.
4. **Shared default for the capability.** The most recent `active` spec with matching `capability_id` and `tenant_scope` in (`<tenant>`, `shared`).
5. **Global fallback.** The shared `deep_orchestrated_v1` spec.

Selector decisions are cached in `adaptive_selection_decisions` keyed by `(tenant_id, capability_id, agent_mode, profile_key, business_context_hash)`. The TTL is controlled by `adaptive_decisions_ttl_days` (default 30 days). The cache is invalidated when a new version of the selected spec is promoted to `active`, or when the tenant policy file changes.

---

## 8. Spec Lifecycle & Promotion

`StrategySpec.status` is the lifecycle field. Allowed transitions:

```
draft ────► active
draft ────► archived
candidate ─► active        (gated by PromotionEvaluator)
candidate ─► deprecated
candidate ─► archived
active ───► deprecated     (replace with a successor)
active ───► archived       (only if no recent traffic, see PromotionManager)
deprecated ─► archived
```

`PromotionManager` (`backend/agent/strategy/promotion_manager.py`) is the only legal mutator of `status`. It enforces:

- Optimistic locking on the spec document (via `version` integer + `X-Expected-Version` header on the API).
- A rollback cooldown (default 1 hour) — you cannot roll a spec back, then immediately roll it forward again. This avoids flapping.
- A re-promotion gate via `PromotionEvaluator.evaluate(strategy_id, dataset_id, thresholds)` which checks composite-score margins, latency p95, fatal-gate failure rate, and privacy-gate failure rate against `PromotionThresholds`. If any threshold fails, promotion is refused with the failing threshold reported.

Promotion is also surfaced over REST:

| Method | Path | Effect |
|---|---|---|
| `POST` | `/api/v1/strategy-specs/{spec_id}/versions/{version}/promote` | `draft|candidate` → `active` (runs evaluator) |
| `POST` | `/api/v1/strategy-specs/{spec_id}/versions/{version}/deprecate` | `active|candidate` → `deprecated` |
| `POST` | `/api/v1/strategy-specs/{spec_id}/versions/{version}/archive` | `deprecated|candidate` → `archived` |
| `POST` | `/api/v1/strategy-specs/{spec_id}/rollback` | Revert to the previous active version (with cooldown) |
| `GET` | `/api/v1/strategy-specs/{spec_id}/history` | List all versions of the spec |

---

## 9. `SpecStore` Persistence Contract

`SpecStore` (`backend/agent/strategy/spec_store.py`) is the ABC behind `strategy_specs`. Two concrete implementations:

- `InMemorySpecStore` — process-local dict, used in tests.
- `MongoSpecStore` — production. Backed by the `strategy_specs` collection with a compound index on `(strategy_id, version)` and a single index on `spec_hash`.

### Public surface

| Method | Notes |
|---|---|
| `create(spec) -> StrategySpec` | Fails if `(strategy_id, version)` already exists |
| `get(spec_id, version=None) -> StrategySpec` | Returns the latest version when `version is None` |
| `update(spec, expected_version) -> StrategySpec` | Optimistic-locked — raises `VersionConflictError` on mismatch |
| `delete(spec_id) -> None` | Soft delete (sets a `deleted_at` field) |
| `list(tenant_id, capability_id=None, status=None) -> list[StrategySpec]` | Cursor-paginated |
| `history(spec_id) -> list[StrategySpec]` | All versions, newest first |
| `validate(spec) -> list[ValidationIssue]` | Static validation (graph compiles, references resolve, budgets sane) |

`VersionConflictError` is mapped to HTTP `409 Conflict` by the router. The client is expected to retry with the latest `version` (visible in the response envelope).

---

## 10. Authoring Workflow

1. **Draft the YAML** under `backend/config/strategy_specs/<name>.yaml`. Use one of the bundled specs as a template.
2. **Validate locally.** `uv run python -m backend.cli.main strategy inspect <strategy_id>` runs `validate_spec()` and prints any structural issues.
3. **Auto-seed.** Restart the backend with `STRATEGY_SPEC_AUTO_SEED=true`. The seeder upserts the spec into Mongo as `status=draft`.
4. **Evaluate.** `uv run python -m backend.cli.main eval run --strategy <id> --dataset default` runs the evaluation dataset against the new spec. Inspect the composite score and dimension breakdown in `evaluation_results` or via `GET /api/v1/evaluation/leaderboard`.
5. **Promote.** `POST /api/v1/strategy-specs/<id>/versions/<v>/promote` — the promotion evaluator gate kicks in. On failure the response contains the failing threshold.
6. **Observe.** Watch the nightly report and `strategy_runs` for regressions. If the new spec misbehaves in production, hit `POST /api/v1/strategy-specs/<id>/rollback`.

---

## 11. Read Next

- For evaluation scoring and judge calibration that backs the promotion gate → [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md)
- For overnight exploration that produces `candidate` specs → [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md)
- For the runtime engine that executes a spec → [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md)
