# Blueprint 04 — Evaluation, Test Corpus, and Judging System

## 1. Objective

Create a repeatable evaluation system so Quellex / RecallHub can compare strategies automatically.

The system should answer:

- Which strategy gives the best answer for this prompt class?
- Which strategy is fastest without damaging quality?
- Which retrieval pattern finds the right sources?
- Which strategy produces unsupported claims?
- Which strategy fails on legal/matter boundaries?
- Which strategy should be promoted as default?

---

## 2. Test case model

```python
class StrategyTestCase(BaseModel):
    id: str
    dataset_id: str
    title: str
    tenant: Literal["quellex", "recallhub"]
    profile_key: Optional[str]
    active_matter_id: Optional[str]
    capability_id: str
    language: str
    user_prompt: str
    conversation_context: list[dict] = []

    expected_answer_contract_id: Optional[str]
    expected_source_ids: list[str] = []
    expected_source_spans: list[str] = []
    forbidden_source_ids: list[str] = []
    forbidden_phrases: list[str] = []
    required_phrases: list[str] = []
    expected_topics: list[str] = []
    golden_answer: Optional[str] = None
    notes: Optional[str] = None

    scoring_profile: str
    max_latency_ms: Optional[int] = None
    max_cost_eur: Optional[float] = None
```

---

## 3. Dataset examples

### `quellex_legal_core_v1`

| Test case | Capability | Purpose |
|---|---|---|
| `legal_ergaenzungsfragen_fenster_001` | `legal_hearing_questions` | Must produce hearing-ready questions, not generic purchase checklist |
| `legal_gutachten_widerspruch_001` | `legal_contradiction_analysis` | Detect methodological contradictions |
| `legal_ce_kennzeichnung_001` | `legal_matter_qna` | Answer about CE date issue with source anchors |
| `legal_source_audit_001` | `legal_source_audit` | Map questions to documents/spans |
| `legal_ambiguous_immobilienprozess_001` | `legal_hearing_questions` | Resolve ambiguity using active matter evidence |
| `legal_cross_matter_guard_001` | `legal_matter_qna` | Ensure unrelated cases are excluded |

### `recallhub_business_core_v1`

| Test case | Capability | Purpose |
|---|---|---|
| `business_transcripts_lessons_001` | `business_insights` | Extract actionable product/business insights |
| `business_failure_analysis_001` | `business_analysis` | Analyze product failure causes |
| `technical_blueprint_001` | `technical_architecture` | Generate implementation blueprint |
| `finance_amounts_001` | `finance_document_qna` | Accurate numbers and citations |

### `retrieval_regression_v1`

Use this dataset to test pure retrieval without synthesis:

- expected document appears in top 3 / top 5 / top 10
- forbidden unrelated documents do not appear
- duplicate versions are grouped
- boilerplate is removed

---

## 4. Test case storage

Store test cases in both DB and files.

```text
backend/evaluation/datasets/
  quellex_legal_core_v1.yaml
  recallhub_business_core_v1.yaml
  retrieval_regression_v1.yaml
```

DB collection:

```javascript
strategy_test_cases
```

Use file specs for version control and DB copies for scheduling/execution.

---

## 5. Evaluation dimensions

### 5.1 Deterministic metrics

| Metric | How to compute |
|---|---|
| `latency_ms` | End-to-end run time |
| `tokens_input/output` | Sum of LLM calls |
| `llm_call_count` | Count calls in trace |
| `search_operation_count` | Count search ops |
| `retrieval_hit_at_k` | Expected source in top K |
| `forbidden_source_violation` | Forbidden source used or surfaced |
| `citation_coverage` | Claims/questions with source anchors / total |
| `answer_contract_pass` | Required sections/table columns present |
| `required_phrase_coverage` | Required terms/topics present |
| `forbidden_phrase_violation` | Prohibited generic/unsafe language |
| `source_scope_violation` | Cross-matter/profile violation |
| `budget_pass` | Latency/cost/context budgets respected |

### 5.2 LLM judge metrics

| Metric | Prompted judge question |
|---|---|
| `directness` | Does the answer directly satisfy the user request? |
| `completeness` | Are the required topics covered? |
| `groundedness` | Are claims supported by provided evidence? |
| `citation_quality` | Are citations specific and useful? |
| `format_adherence` | Does it follow the answer contract? |
| `legal_practicality` | For legal tasks: is it usable in the intended procedural context? |
| `business_value` | For business tasks: does it produce actionable insights? |
| `conciseness` | Is it concise enough for the requested use? |

---

## 6. Judge protocol

Use at least two judging modes.

### 6.1 Reference-grounded judge

Judge receives:

- user prompt
- answer contract
- evidence cards/source snippets
- response
- test case expectations

Judge does not receive:

- strategy ID
- model name
- cost
- latency

Output:

```json
{
  "scores": {
    "directness": 0.0,
    "completeness": 0.0,
    "groundedness": 0.0,
    "citation_quality": 0.0,
    "format_adherence": 0.0,
    "practicality": 0.0
  },
  "overall_quality": 0.0,
  "fatal_issues": [],
  "improvement_notes": []
}
```

### 6.2 Pairwise blind judge

Used for A/B strategy comparison.

Judge receives response A and B in random order.

Output:

```json
{
  "winner": "a|b|tie",
  "confidence": "low|medium|high",
  "reason_codes": ["better_grounding", "better_format", "more_complete"],
  "quality_a": 0.0,
  "quality_b": 0.0
}
```

### 6.3 Judge calibration

Create 20 manually reviewed seed cases.

For each judge model:

- compare judge score with human score
- record bias patterns
- reject judge model if inconsistent on high-stakes legal grounding

---

## 7. Composite scoring

Define scoring profiles per capability.

### Legal hearing questions score

```yaml
weights:
  groundedness: 0.25
  citation_quality: 0.20
  format_adherence: 0.15
  practicality: 0.15
  completeness: 0.10
  directness: 0.10
  latency_normalized: 0.05
fatal_failures:
  - source_scope_violation
  - forbidden_source_violation
  - unsupported_legal_claim_high_confidence
  - no_citations_when_required
```

### Fast general RAG score

```yaml
weights:
  directness: 0.25
  groundedness: 0.25
  citation_quality: 0.15
  latency_normalized: 0.20
  completeness: 0.10
  conciseness: 0.05
```

### Business insight score

```yaml
weights:
  business_value: 0.25
  completeness: 0.20
  actionability: 0.20
  groundedness: 0.15
  structure: 0.10
  latency_normalized: 0.10
```

---

## 8. Evaluation runner

```text
backend/evaluation/runner.py
```

```python
class EvaluationRunner:
    async def run_dataset(
        self,
        dataset_id: str,
        strategy_ids: list[str],
        profile_key: Optional[str],
        options: EvaluationOptions,
    ) -> EvaluationRunResult:
        ...
```

### Execution flow

```text
for test_case in dataset:
  for strategy in strategies:
    run strategy
    collect telemetry
    run deterministic validators
    run judge if enabled
    compute composite score
    persist result
```

---

## 9. Result schema

```python
class StrategyEvaluationResult(BaseModel):
    run_id: str
    experiment_id: Optional[str]
    dataset_id: str
    test_case_id: str
    strategy_id: str
    strategy_version: str
    strategy_spec_hash: str
    response_record_id: str

    deterministic_scores: dict[str, float]
    judge_scores: dict[str, float]
    composite_score: float
    fatal_failures: list[str]
    warnings: list[str]

    latency_ms: int
    token_usage: dict[str, int]
    cost_eur: Optional[float]
    source_ids_used: list[str]
    citation_coverage: float
    created_at: datetime
```

---

## 10. Acceptance criteria

- At least two datasets exist: `quellex_legal_core_v1` and `recallhub_business_core_v1`.
- CLI can run one dataset against one or more strategies.
- Deterministic validators run without LLM dependency.
- LLM judge can be enabled/disabled.
- Every evaluation result links back to telemetry record and strategy spec hash.
- Strategy leaderboard can be computed by dataset, capability, and hardware profile.
