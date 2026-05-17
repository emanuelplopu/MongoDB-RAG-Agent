# Blueprint 07 — Telemetry, Profiling, and Analytics Extensions

## 1. Objective

Extend the existing telemetry system into a full strategy profiling and analytics layer.

The current telemetry system already captures:

- LLM calls
- search operations
- tool executions
- phase metrics
- prompt/response metadata
- PII-protected and raw JSONL modes

Add experiment, strategy, evidence, judging, and runtime resource metadata.

---

## 2. Extended telemetry record fields

Add to `TelemetryRecord`:

```python
class TelemetryRecord(BaseModel):
    # existing fields...

    strategy_id: Optional[str]
    strategy_version: Optional[str]
    strategy_spec_hash: Optional[str]
    strategy_status: Optional[str]
    capability_id: Optional[str]
    answer_contract_id: Optional[str]

    experiment_id: Optional[str]
    experiment_run_id: Optional[str]
    schedule_id: Optional[str]
    dataset_id: Optional[str]
    test_case_id: Optional[str]

    source_policy: Optional[dict]
    retrieval_policy: Optional[dict]
    evidence_policy: Optional[dict]
    validation_policy: Optional[dict]
    budgets: Optional[dict]

    strategy_node_metrics: list[StrategyNodeMetric] = []
    evidence_cards: list[dict] = []
    validation_results: list[dict] = []
    judge_results: list[dict] = []
    resource_snapshots: list[ResourceSnapshot] = []

    replay: Optional[ReplayMetadata]
```

---

## 3. Node-level metrics

```python
class StrategyNodeMetric(BaseModel):
    node_id: str
    node_type: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    model_role: Optional[str]
    model_used: Optional[str]
    prompt_id: Optional[str]
    prompt_version: Optional[str]
    input_tokens: int = 0
    output_tokens: int = 0
    search_count: int = 0
    sources_in: int = 0
    sources_out: int = 0
    success: bool
    error: Optional[str]
```

This replaces coarse phase-only analysis with comparable node timings.

---

## 4. Evidence card telemetry

For each evidence card, store:

```python
class EvidenceCardTelemetry(BaseModel):
    card_id: str
    card_type: str
    topic: str
    source_id: str
    document_title: str
    chunk_id: Optional[str]
    span_id: Optional[str]
    confidence: float
    used_in_final_answer: bool
    final_answer_claim_ids: list[str]
```

Do not store raw PII in protected telemetry. Use existing pseudonymization.

---

## 5. Validation telemetry

```python
class ValidationResultTelemetry(BaseModel):
    validator_id: str
    passed: bool
    score: Optional[float]
    fatal: bool = False
    issues: list[dict] = []
```

Validators:

- `answer_contract_validator`
- `citation_coverage_validator`
- `unsupported_claim_validator`
- `source_scope_validator`
- `latency_budget_validator`
- `format_validator`
- `forbidden_source_validator`
- `privacy_policy_validator`

---

## 6. Runtime resource snapshots

Add runtime profiling during strategy runs.

```python
class ResourceSnapshot(BaseModel):
    timestamp: datetime
    cpu_percent: Optional[float]
    ram_used_gb: Optional[float]
    ram_percent: Optional[float]
    gpu_name: Optional[str]
    gpu_vram_used_gb: Optional[float]
    gpu_utilization_percent: Optional[float]
    model_loaded: Optional[str]
    ollama_ps: Optional[list[dict]]
```

Collect snapshots:

- before run
- after model load
- after retrieval
- after synthesis
- after completion

For local Ollama, parse `ollama ps` where available.

---

## 7. Strategy analytics views

Add analytics endpoints:

```text
GET /api/v1/strategy-analytics/leaderboard
GET /api/v1/strategy-analytics/by-capability/{capability_id}
GET /api/v1/strategy-analytics/regressions
GET /api/v1/strategy-analytics/latency-distribution
GET /api/v1/strategy-analytics/citation-quality
GET /api/v1/strategy-analytics/resource-profile
GET /api/v1/strategy-analytics/promotions
```

---

## 8. Telemetry viewer extensions

Extend the standalone telemetry viewer with pages:

| Page | Purpose |
|---|---|
| Strategy Runs | Browse by strategy, version, capability, dataset |
| Leaderboard | Rank strategies by composite score |
| Regression | Show performance/quality deltas vs previous baseline |
| Retrieval Debug | Compare raw retrieval, reranked results, excluded results |
| Evidence Cards | Inspect cards and final-answer usage |
| Validation | Show answer contract/citation/source-scope failures |
| Resource Profile | Model load, tokens/s, RAM/GPU pressure |
| Replay | Select a record and rerun with another strategy |

---

## 9. Report generation

Add report service:

```text
backend/services/strategy_report_service.py
```

Outputs:

- Markdown
- JSON
- optional HTML later

Report types:

```text
nightly_exploration_report
regression_report
strategy_comparison_report
hardware_runtime_profile_report
retrieval_quality_report
```

---

## 10. Key analytics formulas

### Citation coverage

```text
citation_coverage = source_supported_claims / total_factual_claims
```

For legal question tables:

```text
citation_coverage = rows_with_source_anchor / total_rows
```

### Latency normalized score

```text
if latency <= target: 1.0
if latency >= hard_limit: 0.0
else: 1 - ((latency - target) / (hard_limit - target))
```

### Composite strategy score

```text
composite = weighted_quality_score * latency_score * privacy_gate * fatal_failure_gate
```

Where:

```text
fatal_failure_gate = 0 if any fatal failure else 1
privacy_gate = 0 if privacy/source policy violated else 1
```

---

## 11. Telemetry retention

Keep existing dual-mode PII policy.

Add retention classes:

| Data | Suggested retention |
|---|---:|
| Raw telemetry | 7-30 days, dev/beta only |
| Protected telemetry | 90-365 days |
| Aggregated metrics | indefinite |
| Evaluation results | indefinite unless deleted |
| Evidence cards | same as telemetry mode |
| Reports | configurable |

---

## 12. Acceptance criteria

- Every strategy run has node-level metrics.
- Every experiment result links to a telemetry record.
- Telemetry contains strategy spec hash and answer contract ID.
- CLI can summarize strategy latency, quality, citation coverage, and resource usage.
- Telemetry viewer can compare at least two strategy runs side-by-side.
- Reports can be generated without frontend container.
