# Blueprint 09 — Database and API Contracts

## 1. Objective

Define the database collections and API endpoints needed for Strategy Engine V2, evaluation, scheduling, CLI, telemetry, and promotion workflows.

---

## 2. New collections

### 2.1 `strategy_specs`

```javascript
{
  "_id": "legal_hearing_questions__fast_evidence__v1",
  "id": "legal_hearing_questions__fast_evidence__v1",
  "version": "1.0.0",
  "display_name": "Legal Hearing Questions — Fast Evidence",
  "tenant_scope": ["quellex"],
  "capability_ids": ["legal_hearing_questions"],
  "domains": ["legal"],
  "status": "active",
  "spec": {},
  "spec_hash": "sha256:...",
  "created_at": ISODate,
  "updated_at": ISODate,
  "created_by": "system|user_id"
}
```

Indexes:

```text
id unique
status + tenant_scope
capability_ids
spec_hash
```

---

### 2.2 `business_capabilities`

```javascript
{
  "_id": "legal_hearing_questions",
  "tenant_scope": ["quellex"],
  "domain": "legal",
  "display_name": "Legal hearing questions",
  "description": "Generate Ergänzungsfragen/Nachhakfragen for legal hearings",
  "default_answer_contract_id": "legal_hearing_questions_table",
  "default_strategy_id": "legal_hearing_questions__fast_evidence__v1",
  "detection_rules": {},
  "source_policy_defaults": {},
  "created_at": ISODate
}
```

---

### 2.3 `answer_contracts`

```javascript
{
  "_id": "legal_hearing_questions_table",
  "id": "legal_hearing_questions_table",
  "version": "1.0.0",
  "tenant_scope": ["quellex"],
  "format_id": "legal_hearing_questions_table",
  "spec": {},
  "created_at": ISODate,
  "updated_at": ISODate
}
```

---

### 2.4 `strategy_test_cases`

```javascript
{
  "_id": "legal_ergaenzungsfragen_fenster_001",
  "dataset_id": "quellex_legal_core_v1",
  "tenant": "quellex",
  "profile_key": "rag_test_law",
  "active_matter_id": "covic_sozialbau_fenster",
  "capability_id": "legal_hearing_questions",
  "language": "de",
  "user_prompt": "ich suche ergänzungsfragen von einen immobilienprozess",
  "expected_answer_contract_id": "legal_hearing_questions_table",
  "expected_source_ids": [],
  "forbidden_source_ids": [],
  "required_phrases": [],
  "forbidden_phrases": ["Immobilienkauf Checkliste"],
  "scoring_profile": "legal_hearing_questions_default",
  "created_at": ISODate
}
```

---

### 2.5 `strategy_experiments`

```javascript
{
  "_id": "exp_20260517_legal_nightly",
  "name": "Nightly legal core exploration",
  "mode": "exploration|regression|profiling|smoke",
  "tenant": "quellex",
  "profile_key": "rag_test_law",
  "datasets": ["quellex_legal_core_v1"],
  "strategy_ids": [],
  "candidate_generator_id": "grid_legal_fast",
  "status": "queued|running|completed|failed|cancelled",
  "started_at": ISODate,
  "completed_at": ISODate,
  "summary": {},
  "created_by": "user_id|system"
}
```

---

### 2.6 `strategy_evaluation_results`

```javascript
{
  "_id": ObjectId,
  "experiment_id": "exp_...",
  "dataset_id": "quellex_legal_core_v1",
  "test_case_id": "legal_ergaenzungsfragen_fenster_001",
  "strategy_id": "legal_hearing_questions__fast_evidence__v1",
  "strategy_version": "1.0.0",
  "strategy_spec_hash": "sha256:...",
  "telemetry_record_id": "uuid",
  "deterministic_scores": {},
  "judge_scores": {},
  "composite_score": 0.86,
  "fatal_failures": [],
  "warnings": [],
  "latency_ms": 18400,
  "token_usage": {},
  "cost_eur": null,
  "citation_coverage": 0.92,
  "source_scope_pass": true,
  "created_at": ISODate
}
```

Indexes:

```text
experiment_id
strategy_id + dataset_id
capability_id if duplicated into result
test_case_id
created_at
```

---

### 2.7 `strategy_schedules`

```javascript
{
  "_id": "overnight-legal-core",
  "name": "Overnight legal core exploration",
  "enabled": true,
  "tenant": "quellex",
  "profile_key": "rag_test_law",
  "schedule_type": "cron",
  "cron": "0 22 * * *",
  "timezone": "Europe/Vienna",
  "allowed_window": "22:00-06:00",
  "mode": "exploration",
  "datasets": ["quellex_legal_core_v1"],
  "strategy_ids": [],
  "candidate_generator_id": "grid_legal_fast",
  "resource_limits": {},
  "stop_conditions": {},
  "report_config": {},
  "last_run_at": ISODate,
  "next_run_at": ISODate,
  "created_at": ISODate
}
```

---

### 2.8 `runtime_model_profiles`

```javascript
{
  "_id": ObjectId,
  "host_id": "machine_hash",
  "tenant": "quellex",
  "provider": "ollama",
  "model": "gemma4:26b",
  "context_tokens": 6000,
  "output_tokens": 1200,
  "first_token_ms": 2200,
  "total_latency_ms": 84000,
  "tokens_per_second": 15.6,
  "ram_peak_gb": 44.2,
  "vram_peak_gb": 22.1,
  "cpu_avg_pct": 78.0,
  "gpu_avg_pct": 92.0,
  "success": true,
  "created_at": ISODate
}
```

---

## 3. API endpoints

### 3.1 Strategy specs

```text
GET    /api/v1/strategy-specs
POST   /api/v1/strategy-specs
GET    /api/v1/strategy-specs/{id}
PUT    /api/v1/strategy-specs/{id}
DELETE /api/v1/strategy-specs/{id}
POST   /api/v1/strategy-specs/{id}/validate
POST   /api/v1/strategy-specs/{id}/clone
POST   /api/v1/strategy-specs/{id}/activate
POST   /api/v1/strategy-specs/{id}/promote
GET    /api/v1/strategy-specs/{id}/metrics
```

### 3.2 Run strategy

```text
POST /api/v1/strategy-runs/run-prompt
POST /api/v1/strategy-runs/run-test-case
GET  /api/v1/strategy-runs/{record_id}
```

Run prompt request:

```json
{
  "tenant": "quellex",
  "profile_key": "rag_test_law",
  "active_matter_id": "covic_sozialbau_fenster",
  "strategy_id": "legal_hearing_questions__fast_evidence__v1",
  "user_prompt": "ich suche ergänzungsfragen von einen immobilienprozess",
  "agent_mode": "auto",
  "privacy_mode": "local_only",
  "stream": false
}
```

---

### 3.3 Datasets and evaluation

```text
GET    /api/v1/evaluation/datasets
POST   /api/v1/evaluation/datasets
GET    /api/v1/evaluation/datasets/{id}
POST   /api/v1/evaluation/datasets/{id}/validate
POST   /api/v1/evaluation/run-dataset
GET    /api/v1/evaluation/runs
GET    /api/v1/evaluation/runs/{id}
GET    /api/v1/evaluation/leaderboard
POST   /api/v1/evaluation/compare
```

---

### 3.4 Schedules

```text
GET    /api/v1/strategy-schedules
POST   /api/v1/strategy-schedules
GET    /api/v1/strategy-schedules/{id}
PUT    /api/v1/strategy-schedules/{id}
DELETE /api/v1/strategy-schedules/{id}
POST   /api/v1/strategy-schedules/{id}/toggle
POST   /api/v1/strategy-schedules/{id}/run-now
GET    /api/v1/strategy-schedules/{id}/logs
```

---

### 3.5 Runtime profiler

```text
POST /api/v1/runtime-profiler/model-test
POST /api/v1/runtime-profiler/strategy-profile
GET  /api/v1/runtime-profiler/results
GET  /api/v1/runtime-profiler/models/{model}/summary
```

---

## 4. Backward compatibility

Keep existing endpoints:

```text
/api/v1/strategies
/api/v1/prompts
/api/v1/admin/telemetry
/api/v1/benchmark
```

Add adapters so legacy strategies appear as StrategySpecs.

---

## 5. Acceptance criteria

- Collections can be created with indexes via migration script.
- CLI and API can list, validate, run, and compare strategies.
- A schedule can trigger an experiment and persist results.
- Strategy results link to telemetry records.
- Existing chat flow still works with default strategy.
