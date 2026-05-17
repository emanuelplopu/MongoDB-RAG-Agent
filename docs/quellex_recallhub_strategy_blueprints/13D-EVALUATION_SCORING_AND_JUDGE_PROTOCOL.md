# Blueprint 13D — Evaluation Scoring and Judge Protocol

> Resolves specification gaps: **B3-1** (Composite scoring formula), **B3-2** (Judge determinism and calibration gate), **B3-3** (Test case lifecycle and versioning), **EC-4** (Contradictory answer detection).

---

## 1. Composite Scoring Formula — Complete Specification

### 1.1 Normalization

All dimension scores (from LLM judge or deterministic validator) are normalized to the closed interval `[0.0, 1.0]` before weighting. Raw judge output outside this range is clamped:

```
normalized = max(0.0, min(1.0, raw_score))
```

### 1.2 Fatal Failure Gate

Binary multiplier: **0** or **1**.

Fatal failures that set gate to **0**:

| Fatal Failure Code | Description |
|---|---|
| `forbidden_source_violation` | Response uses or surfaces a forbidden source document |
| `cross_matter_leak` | Response references content from a different legal matter |
| `privacy_policy_violation` | PII or privileged content leaked outside policy boundary |
| `citations_disabled_when_required` | Strategy omitted citations when scoring profile requires them |

```
fatal_gate = 0 if ANY fatal failure detected else 1
```

### 1.3 Privacy Gate

Binary multiplier: **0** or **1**.

Evaluates whether the judging strategy itself complies with the tenant's privacy mode:

| Condition | Gate Value |
|---|---|
| Tenant `privacy_mode = local_only` AND judge uses cloud LLM | **0** |
| Tenant `privacy_mode = local_only` AND judge uses local Ollama model | **1** |
| Tenant `privacy_mode = cloud_ok` | **1** (always) |

```
privacy_gate = 0 if judge_violates_tenant_privacy_mode else 1
```

### 1.4 Latency Normalized Score

Piecewise linear degradation between target and hard limit:

```
if actual_latency_ms <= latency_target_ms:
    latency_score = 1.0
elif actual_latency_ms >= latency_hard_limit_ms:
    latency_score = 0.0
else:
    latency_score = 1.0 - (
        (actual_latency_ms - latency_target_ms) /
        (latency_hard_limit_ms - latency_target_ms)
    )
```

Default thresholds per profile (overridable via tenant config):

| Profile | `latency_target_ms` | `latency_hard_limit_ms` |
|---|---|---|
| `legal_hearing` | 8000 | 45000 |
| `legal_matter` | 5000 | 30000 |
| `fast_general` | 2000 | 10000 |
| `business_insights` | 6000 | 35000 |

### 1.5 Composite Formula

```
composite = (Σ score_i × weight_i) × latency_score × fatal_gate × privacy_gate
```

Where:
- `score_i` = normalized score for dimension `i`
- `weight_i` = weight for dimension `i` from the scoring profile
- All weights per profile MUST sum to **1.0**

### 1.6 Scoring Dimensions and Default Weights

| Dimension | `legal_hearing` | `legal_matter` | `fast_general` | `business_insights` |
|---|---|---|---|---|
| `groundedness` | 0.25 | 0.30 | 0.25 | 0.15 |
| `citation_quality` | 0.20 | 0.25 | 0.10 | 0.10 |
| `directness` | 0.10 | 0.10 | 0.25 | 0.15 |
| `completeness` | 0.15 | 0.15 | 0.15 | 0.20 |
| `format_adherence` | 0.15 | 0.05 | 0.10 | 0.10 |
| `domain_value` | 0.10 | 0.10 | 0.05 | 0.25 |
| `conciseness` | 0.05 | 0.05 | 0.10 | 0.05 |
| **Total** | **1.00** | **1.00** | **1.00** | **1.00** |

Dimension definitions:

| Dimension | Measurement Source | Description |
|---|---|---|
| `groundedness` | LLM judge | Are all claims supported by provided evidence? |
| `citation_quality` | LLM judge + deterministic | Are citations specific, correctly anchored, and verifiable? |
| `directness` | LLM judge | Does the answer directly address the user's request? |
| `completeness` | LLM judge + deterministic | Are required topics, sections, and details covered? |
| `format_adherence` | Deterministic + judge | Does the response follow the answer contract format? |
| `domain_value` | LLM judge | Is the answer actionable in its domain context (legal/business)? |
| `conciseness` | LLM judge | Is the response appropriately concise without sacrificing completeness? |

### 1.7 Worked Example #1 — Legal Hearing Questions (Pass)

**Input scores:**

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| groundedness | 0.92 | 0.25 | 0.2300 |
| citation_quality | 0.88 | 0.20 | 0.1760 |
| directness | 0.85 | 0.10 | 0.0850 |
| completeness | 0.78 | 0.15 | 0.1170 |
| format_adherence | 1.00 | 0.15 | 0.1500 |
| domain_value | 0.90 | 0.10 | 0.0900 |
| conciseness | 0.85 | 0.05 | 0.0425 |

**Weighted sum:** 0.2300 + 0.1760 + 0.0850 + 0.1170 + 0.1500 + 0.0900 + 0.0425 = **0.8905**

**Latency calculation:**
- `actual_latency_ms` = 12000
- `latency_target_ms` = 8000
- `latency_hard_limit_ms` = 45000
- `latency_score` = 1.0 − (12000 − 8000) / (45000 − 8000) = 1.0 − 4000/37000 = **0.892**

**Gates:**
- `fatal_gate` = 1 (no fatal failures detected)
- `privacy_gate` = 1 (judge compliant with tenant privacy mode)

**Composite:** 0.8905 × 0.892 × 1 × 1 = **0.794**

### 1.8 Worked Example #2 — Fatal Failure

Same scores as Example #1, but `forbidden_source_violation` detected in response.

- `fatal_gate` = **0**
- Composite: 0.8905 × 0.892 × **0** × 1 = **0.000**

The response is automatically ranked last regardless of quality scores.

### 1.9 Worked Example #3 — Privacy Violation

Strategy used cloud-based GPT-4o judge on a `local_only` tenant.

- `privacy_gate` = **0**
- Composite: 0.8905 × 0.892 × 1 × **0** = **0.000**

The evaluation result is flagged as invalid and excluded from leaderboard.

### 1.10 Pydantic Models

```python
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class DimensionScore(BaseModel):
    """Individual dimension score from judge or deterministic validator."""
    dimension: str = Field(..., description="Dimension name, e.g. 'groundedness'")
    raw_score: float = Field(..., ge=0.0, le=1.0)
    source: str = Field(..., description="'judge' | 'deterministic' | 'hybrid'")
    rationale: Optional[str] = Field(None, description="Judge's reasoning for this score")
    variance: Optional[float] = Field(
        None, ge=0.0, description="Variance across multiple judge runs (if applicable)"
    )


class ScoringProfile(BaseModel):
    """Defines weight distribution and thresholds for a capability profile."""
    profile_id: str = Field(..., description="e.g. 'legal_hearing'")
    display_name: str
    weights: dict[str, float] = Field(
        ..., description="Dimension name → weight. Must sum to 1.0"
    )
    latency_target_ms: int = Field(..., ge=0)
    latency_hard_limit_ms: int = Field(..., gt=0)
    fatal_failures: list[str] = Field(
        default_factory=lambda: [
            "forbidden_source_violation",
            "cross_matter_leak",
            "privacy_policy_violation",
            "citations_disabled_when_required",
        ]
    )
    judge_calibrations: Optional[dict] = Field(
        None, description="Stored calibration results for this profile"
    )

    @field_validator("weights")
    @classmethod
    def weights_must_sum_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        return v


class LatencyResult(BaseModel):
    """Latency measurement and normalized score."""
    actual_ms: int
    target_ms: int
    hard_limit_ms: int
    normalized_score: float = Field(..., ge=0.0, le=1.0)


class CompositeResult(BaseModel):
    """Final composite evaluation result."""
    run_id: str
    test_case_id: str
    test_case_version: int
    strategy_id: str
    profile_id: str

    dimension_scores: list[DimensionScore]
    weighted_sum: float = Field(..., ge=0.0, le=1.0)
    latency: LatencyResult
    fatal_gate: int = Field(..., ge=0, le=1)
    fatal_failures_detected: list[str] = Field(default_factory=list)
    privacy_gate: int = Field(..., ge=0, le=1)
    privacy_violation_reason: Optional[str] = None

    composite_score: float = Field(..., ge=0.0, le=1.0)
    computed_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("fatal_gate", "privacy_gate")
    @classmethod
    def must_be_binary(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("Gate must be 0 or 1")
        return v
```

### 1.11 CompositeScoreCalculator

```python
class CompositeScoreCalculator:
    """Computes composite scores from dimension scores, latency, and gates."""

    def __init__(self, profile: ScoringProfile):
        self.profile = profile

    def compute_latency_score(self, actual_ms: int) -> LatencyResult:
        target = self.profile.latency_target_ms
        hard_limit = self.profile.latency_hard_limit_ms

        if actual_ms <= target:
            score = 1.0
        elif actual_ms >= hard_limit:
            score = 0.0
        else:
            score = 1.0 - ((actual_ms - target) / (hard_limit - target))

        return LatencyResult(
            actual_ms=actual_ms,
            target_ms=target,
            hard_limit_ms=hard_limit,
            normalized_score=round(score, 4),
        )

    def compute_fatal_gate(self, detected_failures: list[str]) -> int:
        for failure in detected_failures:
            if failure in self.profile.fatal_failures:
                return 0
        return 1

    def compute_privacy_gate(
        self, tenant_privacy_mode: str, judge_mode: str
    ) -> tuple[int, Optional[str]]:
        if tenant_privacy_mode == "local_only" and judge_mode == "cloud":
            return 0, f"Cloud judge used in local_only tenant"
        return 1, None

    def compute(
        self,
        dimension_scores: list[DimensionScore],
        actual_latency_ms: int,
        detected_failures: list[str],
        tenant_privacy_mode: str,
        judge_mode: str,
        run_id: str,
        test_case_id: str,
        test_case_version: int,
        strategy_id: str,
    ) -> CompositeResult:
        # Compute weighted sum
        weighted_sum = 0.0
        for ds in dimension_scores:
            weight = self.profile.weights.get(ds.dimension, 0.0)
            clamped = max(0.0, min(1.0, ds.raw_score))
            weighted_sum += clamped * weight
        weighted_sum = round(weighted_sum, 4)

        # Compute latency
        latency = self.compute_latency_score(actual_latency_ms)

        # Compute gates
        fatal_gate = self.compute_fatal_gate(detected_failures)
        privacy_gate, privacy_reason = self.compute_privacy_gate(
            tenant_privacy_mode, judge_mode
        )

        # Final composite
        composite = round(
            weighted_sum * latency.normalized_score * fatal_gate * privacy_gate, 4
        )

        return CompositeResult(
            run_id=run_id,
            test_case_id=test_case_id,
            test_case_version=test_case_version,
            strategy_id=strategy_id,
            profile_id=self.profile.profile_id,
            dimension_scores=dimension_scores,
            weighted_sum=weighted_sum,
            latency=latency,
            fatal_gate=fatal_gate,
            fatal_failures_detected=[
                f for f in detected_failures if f in self.profile.fatal_failures
            ],
            privacy_gate=privacy_gate,
            privacy_violation_reason=privacy_reason,
            composite_score=composite,
        )
```

---

## 2. Judge Determinism and Calibration Gate

### 2.1 Determinism Requirements

LLM judges are inherently stochastic. The following measures minimize variance:

| Parameter | OpenAI Models | Ollama Models |
|---|---|---|
| `temperature` | 0.0 | 0.0 |
| `seed` | 42 | Set in `options.seed` if supported |
| `top_p` | 1.0 (default) | 1.0 |

**Acceptable variance threshold:** ±0.05 per dimension across 3 independent runs.

**Variance handling protocol:**

```
for each (test_case, response) pair:
    scores = [run_judge() for _ in range(3)]
    for dimension in all_dimensions:
        dim_scores = [s[dimension] for s in scores]
        variance = max(dim_scores) - min(dim_scores)
        if variance > 0.05:
            log.warning(
                f"Judge variance {variance:.3f} on {dimension} exceeds threshold. "
                f"Using mean of 3 runs."
            )
            final_score[dimension] = mean(dim_scores)
        else:
            final_score[dimension] = scores[0][dimension]  # Use first run
```

**For Ollama models without seed support:**

Always run judge 3 times and take the **median** score per dimension. This accounts for non-deterministic sampling that cannot be controlled via seed.

### 2.2 Calibration Gate Protocol

The calibration gate ensures that the judge model's scoring correlates with human expert judgment before it is trusted for automated evaluation.

#### Step 1: Human Baseline Creation

- Domain expert (tenant admin or legal team lead) manually scores **20 seed cases**
- Each case is scored on all 7 dimensions using the same 0.0–1.0 scale
- Scores are recorded with brief rationale per dimension
- Seed cases should cover the full range: include excellent, mediocre, and poor responses

#### Step 2: Judge Model Scoring

- Same 20 cases are scored by the candidate judge model
- Judge uses the same prompt template and scoring rubric
- Each case run 3 times; median used as final score

#### Step 3: Correlation Computation

For each dimension `d`:

```
r_d = pearson_correlation(human_scores_d, judge_scores_d)
```

Where `pearson_correlation` is the standard Pearson product-moment correlation coefficient:

```
r = Σ((x_i - x̄)(y_i - ȳ)) / sqrt(Σ(x_i - x̄)² × Σ(y_i - ȳ)²)
```

#### Step 4: Gate Decision

| Condition | Result |
|---|---|
| All dimensions `r_d > 0.85` | **PASS** — judge model approved for this scoring profile |
| Any dimension `r_d ≤ 0.85` | **REJECT** — judge model not suitable for this profile |
| Any dimension with < 5 non-zero human scores | **INSUFFICIENT DATA** — add more seed cases |

#### Step 5: Rejection Recovery

If a judge model is rejected:

1. Try alternative judge model (e.g., switch from `llama3.1:8b` to `llama3.1:70b`)
2. Adjust judge prompt (add dimension-specific examples, refine rubric)
3. Re-run calibration from Step 2
4. If 3 consecutive judge models fail calibration: escalate to manual review of scoring rubric

#### Step 6: Calibration Storage

Calibration results are stored in the `judge_calibrations` sub-document of the scoring profile:

```json
{
  "profile_id": "legal_hearing",
  "judge_calibrations": {
    "model": "gpt-4o-2024-08-06",
    "calibrated_at": "2026-04-15T10:30:00Z",
    "seed_case_count": 20,
    "correlations": {
      "groundedness": 0.93,
      "citation_quality": 0.89,
      "directness": 0.91,
      "completeness": 0.87,
      "format_adherence": 0.95,
      "domain_value": 0.88,
      "conciseness": 0.92
    },
    "gate_status": "passed",
    "calibrated_by": "legal_team_lead@firm.com",
    "variance_report": {
      "max_variance_observed": 0.04,
      "dimensions_with_high_variance": []
    }
  }
}
```

### 2.3 Recalibration Triggers

| Trigger | Action |
|---|---|
| Judge model changed (new version or different model) | Full recalibration required |
| Test case corpus updated (>20% of cases modified) | Full recalibration required |
| Scoring profile weights changed | Re-run correlation check (no new human scores needed unless rubric changed) |
| Manual trigger | `quellexctl judge recalibrate --profile <profile_id>` |
| Quarterly schedule | Automatic reminder; recalibration recommended |

### 2.4 Judge Prompt Structure

#### System Prompt Template

```text
You are an expert evaluation judge for a legal/business RAG system.

Your role: Score a strategy response on 7 quality dimensions.

## Scoring Rubric

For each dimension, assign a score from 0.0 to 1.0:

### groundedness (0.0–1.0)
- 1.0: Every factual claim is directly supported by the provided source documents
- 0.7: Most claims supported; minor inferences reasonable
- 0.4: Multiple claims lack source support
- 0.0: Majority of claims are unsupported or hallucinated

### citation_quality (0.0–1.0)
- 1.0: Citations are specific (document + section/paragraph), correctly anchored, verifiable
- 0.7: Citations present and mostly correct; some lack specificity
- 0.4: Citations present but vague or partially incorrect
- 0.0: No citations or citations are fabricated

### directness (0.0–1.0)
- 1.0: Answer immediately addresses the user's request without preamble
- 0.7: Addresses request with minor tangential content
- 0.4: Partially addresses request; significant irrelevant content
- 0.0: Does not address the user's actual question

### completeness (0.0–1.0)
- 1.0: All required topics, sections, and details fully covered
- 0.7: Major topics covered; minor gaps
- 0.4: Significant topics missing
- 0.0: Fundamentally incomplete

### format_adherence (0.0–1.0)
- 1.0: Perfectly follows the answer contract format (headings, tables, structure)
- 0.7: Mostly follows format; minor deviations
- 0.4: Recognizable attempt at format; significant deviations
- 0.0: Ignores required format entirely

### domain_value (0.0–1.0)
- 1.0: Directly actionable in the professional context (courtroom-ready, board-ready)
- 0.7: Professionally useful with minor adjustments needed
- 0.4: Generic; requires significant rework for professional use
- 0.0: Not usable in professional context

### conciseness (0.0–1.0)
- 1.0: Optimal length; every sentence adds value
- 0.7: Slightly verbose but acceptable
- 0.4: Notably verbose; significant filler content
- 0.0: Extremely verbose or padded with irrelevant content

## Output Format

Respond with ONLY a JSON object:
{
  "scores": {
    "groundedness": <float>,
    "citation_quality": <float>,
    "directness": <float>,
    "completeness": <float>,
    "format_adherence": <float>,
    "domain_value": <float>,
    "conciseness": <float>
  },
  "rationale": {
    "groundedness": "<1 sentence>",
    "citation_quality": "<1 sentence>",
    "directness": "<1 sentence>",
    "completeness": "<1 sentence>",
    "format_adherence": "<1 sentence>",
    "domain_value": "<1 sentence>",
    "conciseness": "<1 sentence>"
  },
  "fatal_issues": []
}

## Rules
- Score based ONLY on the evidence provided
- Do NOT infer quality from response length alone
- Do NOT penalize for information the sources do not contain
- If the response contains cross-matter information not in sources, add "cross_matter_leak" to fatal_issues
- If the response uses forbidden sources, add "forbidden_source_violation" to fatal_issues
```

#### User Prompt Template

```text
## Test Case Query
{user_prompt}

## Answer Contract
{answer_contract_description}

## Source Documents Provided to Strategy
{source_documents_json}

## Strategy Response
{strategy_response}

## Expected Topics (for completeness check)
{expected_topics_list}

## Forbidden Sources (IDs)
{forbidden_source_ids}

Score this response according to your rubric.
```

### 2.5 Pydantic Models

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


class JudgeConfig(BaseModel):
    """Configuration for the evaluation judge."""
    model_name: str = Field(..., description="e.g. 'gpt-4o-2024-08-06' or 'llama3.1:70b'")
    mode: Literal["cloud", "local"] = Field(..., description="Cloud API or local Ollama")
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    seed: Optional[int] = Field(42, description="Set if model supports deterministic seed")
    max_retries: int = Field(3, ge=1, le=5, description="Runs for variance check")
    variance_threshold: float = Field(0.05, ge=0.0, le=0.5)
    use_median_for_seedless: bool = Field(
        True, description="Use median across runs when model doesn't support seed"
    )
    system_prompt_template: str = Field(..., description="Judge system prompt template")
    user_prompt_template: str = Field(..., description="Judge user prompt template")


class DimensionCorrelation(BaseModel):
    """Correlation result for one dimension."""
    dimension: str
    pearson_r: float = Field(..., ge=-1.0, le=1.0)
    p_value: float = Field(..., ge=0.0, le=1.0)
    human_mean: float
    judge_mean: float
    bias: float = Field(..., description="judge_mean - human_mean (positive = judge scores higher)")


class CalibrationGate(BaseModel):
    """Result of calibration gate evaluation."""
    passed: bool
    threshold: float = Field(0.85, description="Minimum Pearson r required")
    failing_dimensions: list[str] = Field(
        default_factory=list, description="Dimensions that failed the threshold"
    )
    recommendation: str = Field(
        ..., description="'approved' | 'rejected_try_alternative' | 'rejected_adjust_prompt'"
    )


class JudgeCalibrationResult(BaseModel):
    """Complete calibration result for a judge model on a scoring profile."""
    profile_id: str
    judge_model: str
    calibrated_at: datetime
    calibrated_by: str = Field(..., description="Email of domain expert who provided human scores")
    seed_case_count: int = Field(..., ge=10)
    correlations: list[DimensionCorrelation]
    gate: CalibrationGate
    variance_report: dict = Field(
        default_factory=dict,
        description="{'max_variance_observed': float, 'dimensions_with_high_variance': list}"
    )
    notes: Optional[str] = None

    @property
    def is_approved(self) -> bool:
        return self.gate.passed
```

---

## 3. Test Case Lifecycle and Versioning

### 3.1 Version Semantics

Each test case maintains a monotonically increasing `version: int` field.

**Version-incrementing changes** (modify evaluation semantics):

| Field | Reason |
|---|---|
| `expected_source_ids` | Changes what "correct retrieval" means |
| `expected_topics` | Changes completeness evaluation |
| `scoring_profile` | Changes how scores are weighted |
| `forbidden_source_ids` | Changes boundary violation detection |
| `required_phrases` | Changes deterministic scoring |
| `forbidden_phrases` | Changes deterministic scoring |
| `golden_answer` | Changes reference baseline |
| `user_prompt` | Changes the actual test input |
| `conversation_context` | Changes multi-turn evaluation setup |
| `answer_contract_id` | Changes format evaluation |

**Non-versioning changes** (metadata only):

| Field | Reason |
|---|---|
| `title` | Display only |
| `notes` | Documentation only |
| `tags` | Organizational only |
| `description` | Documentation only |

### 3.2 Version History

Old versions are **never deleted** — they receive a `deprecated_at` timestamp (soft delete):

```
Active version: latest version WHERE deprecated_at IS NULL
Historical versions: all versions WHERE deprecated_at IS NOT NULL
```

This enables:
- Reproducibility: any historical evaluation result can be re-interpreted against its test case version
- Trend analysis: compare strategy performance across test case evolution
- Audit: legal compliance requires immutable evaluation history

### 3.3 Evaluation Result Linking

Every `StrategyEvaluationResult` stores both identifiers:

```python
test_case_id: str       # Stable identifier
test_case_version: int  # Version at time of evaluation
```

**Leaderboard queries:** filter by `test_case_version = latest_active_version` only.

**Historical comparisons:** specify version range:

```python
# Example: compare strategy performance across test case versions 3-7
results = await get_results(
    test_case_id="legal_ergaenzungsfragen_fenster_001",
    version_range=(3, 7),
    strategy_id="multi_step_legal_v2"
)
```

### 3.4 Stale Document Detection

When documents are ingested or deleted, the system checks all active test cases for stale references.

**Detection flow:**

```
on_document_ingested(doc_id):
    # No action needed — new documents don't invalidate existing expectations

on_document_deleted(doc_id):
    stale_cases = find_test_cases_where(
        expected_source_ids contains doc_id
        AND deprecated_at IS NULL
    )
    for case in stale_cases:
        mark_stale(case, reason=f"Referenced document {doc_id} deleted")

on_document_replaced(old_doc_id, new_doc_id):
    # Same as deletion — the old doc_id reference is now invalid
    stale_cases = find_test_cases_where(
        expected_source_ids contains old_doc_id
        AND deprecated_at IS NULL
    )
    for case in stale_cases:
        mark_stale(case, reason=f"Referenced document {old_doc_id} replaced by {new_doc_id}")
```

**Stale test case behavior:**

| Context | Behavior |
|---|---|
| Automated evaluation run | **Excluded** — stale cases skipped with warning |
| Manual evaluation run | Included if `--include-stale` flag set |
| Nightly report | Listed under "Needs Attention" section |
| Leaderboard | Excluded from active leaderboard |

**Resolution:** Dataset curator updates `expected_source_ids` (creates new version) or deprecates the test case entirely.

### 3.5 Dataset Curator Role

| Permission | Curator | Admin | Regular User |
|---|---|---|---|
| Create test cases | Yes | Yes | No |
| Update test cases (new version) | Yes | Yes | No |
| Deprecate test cases | Yes | Yes | No |
| Delete test cases (hard delete) | **No** | **No** | No |
| Assign other curators | No | Yes | No |
| Run evaluations | Yes | Yes | Read-only |
| View evaluation results | Yes | Yes | Yes |

**Assignment:**

```bash
quellexctl dataset assign-curator \
    --dataset quellex_legal_core_v1 \
    --user legal_team_lead@firm.com
```

**Immutability guarantee:** No user, including admin, can hard-delete test cases or evaluation results. This ensures audit trail integrity for legal compliance.

### 3.6 Pydantic Models

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


class StrategyTestCaseVersioned(BaseModel):
    """Test case with versioning support."""
    id: str = Field(..., description="Stable test case identifier")
    version: int = Field(1, ge=1, description="Auto-incremented on semantic changes")
    dataset_id: str
    title: str
    tenant: Literal["quellex", "recallhub"]
    profile_key: Optional[str] = None
    active_matter_id: Optional[str] = None
    capability_id: str
    language: str
    user_prompt: str
    conversation_context: list[dict] = Field(default_factory=list)

    # Evaluation expectations (version-incrementing)
    expected_answer_contract_id: Optional[str] = None
    expected_source_ids: list[str] = Field(default_factory=list)
    expected_source_spans: list[str] = Field(default_factory=list)
    forbidden_source_ids: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    required_phrases: list[str] = Field(default_factory=list)
    expected_topics: list[str] = Field(default_factory=list)
    golden_answer: Optional[str] = None

    # Scoring configuration (version-incrementing)
    scoring_profile: str
    max_latency_ms: Optional[int] = None
    max_cost_eur: Optional[float] = None

    # Metadata (non-versioning)
    notes: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    description: Optional[str] = None

    # Lifecycle
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(..., description="Email of creator")
    deprecated_at: Optional[datetime] = Field(
        None, description="Set when this version is superseded"
    )
    deprecated_reason: Optional[str] = None
    is_stale: bool = Field(False, description="True if referenced documents are missing")
    stale_reason: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.deprecated_at is None and not self.is_stale


VERSION_INCREMENTING_FIELDS = frozenset({
    "expected_source_ids",
    "expected_source_spans",
    "expected_topics",
    "scoring_profile",
    "forbidden_source_ids",
    "required_phrases",
    "forbidden_phrases",
    "golden_answer",
    "user_prompt",
    "conversation_context",
    "expected_answer_contract_id",
    "max_latency_ms",
    "max_cost_eur",
})


class TestCaseVersionHistory(BaseModel):
    """Summary of version history for a test case."""
    test_case_id: str
    current_version: int
    total_versions: int
    versions: list["TestCaseVersionSummary"]


class TestCaseVersionSummary(BaseModel):
    """Brief record of a single version."""
    version: int
    created_at: datetime
    created_by: str
    deprecated_at: Optional[datetime] = None
    change_summary: str = Field(..., description="Human-readable description of what changed")
    fields_changed: list[str] = Field(
        default_factory=list, description="Which version-incrementing fields were modified"
    )


class StaleTestCaseReport(BaseModel):
    """Report of test cases with stale document references."""
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    total_active_cases: int
    stale_cases: list["StaleTestCaseEntry"]
    datasets_affected: list[str]

    @property
    def stale_percentage(self) -> float:
        if self.total_active_cases == 0:
            return 0.0
        return len(self.stale_cases) / self.total_active_cases * 100


class StaleTestCaseEntry(BaseModel):
    """Individual stale test case entry in report."""
    test_case_id: str
    version: int
    dataset_id: str
    stale_reason: str
    missing_document_ids: list[str]
    became_stale_at: datetime
    suggested_action: Literal["update_sources", "deprecate", "review"]
    curator_email: Optional[str] = None
```

---

## 4. Contradictory Answer Detection

### 4.1 Activation Conditions

| Condition | Detection Active? |
|---|---|
| First turn in session (turn=1) | No |
| Multi-turn session (turn > 1) | Yes |
| Tenant config `detect_contradictions: true` (default) | Yes |
| Tenant config `detect_contradictions: false` | No |
| Session has no previous strategy response | No |

### 4.2 Detection Scope

Compare the **new answer** with the **most recent previous answer in the same session** that addressed a related query. "Related" is determined by:

- Same session ID
- Previous turn answered a query about the same topic/matter
- Both turns produced substantive answers (not clarification requests or errors)

### 4.3 Detection Method

#### Step 1: Claim Extraction

Using the validator model (lightweight, fast), extract key claims from both answers:

```text
System: Extract the key factual claims from the following text as a JSON list.
Each claim should be a short declarative statement.
Return ONLY claims that make specific factual assertions.

User: {answer_text}

Expected output:
["The contract was signed on March 15, 2024",
 "The damage amount is €45,000",
 "The defendant has not responded to the claim"]
```

#### Step 2: Contradiction Detection

For each pair of claims (one from previous, one from new answer):

```text
System: You are a contradiction detector. Given two claims from different responses
in the same conversation, determine if they contradict each other.

Respond with ONLY a JSON object:
{
  "contradicts": true|false,
  "confidence": "high"|"medium"|"low",
  "explanation": "<brief explanation>"
}

User:
Previous claim: "{claim_a}"
New claim: "{claim_b}"
```

Only flag contradictions with `confidence: "high"` or `confidence: "medium"`.

#### Step 3: Attribution

| Condition | Attribution |
|---|---|
| Strategy changed between turns | `strategy_related` — different strategies may interpret evidence differently |
| Same strategy, new documents ingested between turns | `data_related` — new evidence may update conclusions |
| Same strategy, same documents | `model_variance` — LLM non-determinism |

#### Step 4: Decision Matrix

| Contradictions Found | Confidence | Action |
|---|---|---|
| 0 | N/A | No warning; proceed normally |
| 1+ | High | Include `contradiction_warning` in response |
| 1+ | Medium only | Include `contradiction_warning` with softer language |
| 1+ | Low only | Log internally; do not warn user |

### 4.4 User Communication

When a contradiction is detected, the response envelope includes:

```json
{
  "response": "...",
  "contradiction_warning": {
    "detected": true,
    "message_de": "Hinweis: Diese Antwort weicht von einer früheren Antwort in dieser Sitzung ab.",
    "message_en": "Note: This answer differs from a previous answer in this session.",
    "details": {
      "previous_claim": "The contract deadline is December 31, 2025",
      "new_claim": "The contract deadline was extended to March 31, 2026",
      "attribution": "data_related",
      "reason": "New document ingested: 'Amendment_2026-01-15.pdf'"
    }
  }
}
```

The UI displays this as a non-blocking informational banner, not an error.

### 4.5 Performance Budget

| Operation | Estimated Latency | Notes |
|---|---|---|
| Claim extraction (previous) | ~500ms | Cached after first extraction |
| Claim extraction (new) | ~500ms | Run after synthesis completes |
| Pairwise contradiction check | ~1000ms | Batch all pairs in single prompt if ≤10 pairs |
| Total overhead | ~2000ms | Only for turn > 1 |

**Optimization:** Cache claim extraction for previous answers. Only the new answer needs fresh extraction each turn.

**Circuit breaker:** If claim extraction or contradiction check exceeds 3000ms, skip detection and log timeout. Do not block response delivery.

### 4.6 Opt-out Configuration

```yaml
# In tenant policy configuration
tenant_policies:
  quellex_firm_a:
    detect_contradictions: true  # default
    contradiction_confidence_threshold: "medium"  # "high" = stricter, fewer warnings
  quellex_firm_b:
    detect_contradictions: false  # disabled for this tenant
```

### 4.7 Pydantic Models

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


class ExtractedClaim(BaseModel):
    """A single factual claim extracted from a response."""
    claim_text: str
    source_turn: int = Field(..., description="Which conversation turn this claim is from")
    response_id: str = Field(..., description="ID of the response this claim was extracted from")


class ContradictionPair(BaseModel):
    """A detected contradiction between two claims."""
    previous_claim: str
    new_claim: str
    confidence: Literal["high", "medium", "low"]
    explanation: str
    attribution: Literal["strategy_related", "data_related", "model_variance"]
    attribution_reason: Optional[str] = None


class ContradictionCheckResult(BaseModel):
    """Complete result of contradiction detection for a turn."""
    session_id: str
    current_turn: int
    previous_turn: int
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    previous_claims_extracted: int
    new_claims_extracted: int
    pairs_checked: int

    contradictions: list[ContradictionPair] = Field(default_factory=list)
    has_contradiction: bool = Field(False)
    highest_confidence: Optional[Literal["high", "medium", "low"]] = None

    detection_latency_ms: int = Field(..., description="Total time for contradiction check")
    skipped: bool = Field(False, description="True if detection was skipped (timeout/disabled)")
    skip_reason: Optional[str] = None

    # Strategy context
    previous_strategy_id: Optional[str] = None
    current_strategy_id: Optional[str] = None
    strategy_changed: bool = Field(False)
    new_documents_since_previous: list[str] = Field(
        default_factory=list,
        description="Document IDs ingested between previous and current turn"
    )


class ContradictionWarning(BaseModel):
    """Warning payload included in response envelope when contradiction detected."""
    detected: bool = True
    message_de: str = Field(
        default="Hinweis: Diese Antwort weicht von einer früheren Antwort in dieser Sitzung ab."
    )
    message_en: str = Field(
        default="Note: This answer differs from a previous answer in this session."
    )
    details: Optional[ContradictionDetail] = None


class ContradictionDetail(BaseModel):
    """Detail of the most significant contradiction for user display."""
    previous_claim: str
    new_claim: str
    attribution: Literal["strategy_related", "data_related", "model_variance"]
    reason: str = Field(..., description="Human-readable explanation of why the answer changed")
```

---

## 5. Integration Points

### 5.1 Evaluation Runner Integration

The `CompositeScoreCalculator` is invoked by the evaluation runner after all dimension scores are collected:

```python
# In backend/evaluation/runner.py
async def evaluate_single(self, test_case, strategy_id, judge_config):
    # 1. Run strategy
    response = await self.run_strategy(test_case, strategy_id)

    # 2. Collect deterministic scores
    det_scores = await self.run_deterministic_validators(test_case, response)

    # 3. Run judge (if enabled and privacy-compliant)
    judge_scores = await self.run_judge(test_case, response, judge_config)

    # 4. Merge dimension scores
    all_scores = merge_dimension_scores(det_scores, judge_scores)

    # 5. Compute composite
    profile = await self.load_scoring_profile(test_case.scoring_profile)
    calculator = CompositeScoreCalculator(profile)
    result = calculator.compute(
        dimension_scores=all_scores,
        actual_latency_ms=response.latency_ms,
        detected_failures=det_scores.fatal_failures,
        tenant_privacy_mode=self.tenant_config.privacy_mode,
        judge_mode=judge_config.mode,
        run_id=self.run_id,
        test_case_id=test_case.id,
        test_case_version=test_case.version,
        strategy_id=strategy_id,
    )
    return result
```

### 5.2 Contradiction Detection Integration

Contradiction detection is integrated in the chat/session response flow:

```python
# In backend/routers/chat.py or backend/agent/orchestrator.py
async def generate_response(session_id, user_prompt, ...):
    # 1. Normal strategy execution
    response = await orchestrator.run(...)

    # 2. Contradiction check (if applicable)
    if should_check_contradictions(session_id, tenant_config):
        contradiction_result = await contradiction_detector.check(
            session_id=session_id,
            new_response=response,
        )
        if contradiction_result.has_contradiction:
            response.contradiction_warning = build_warning(contradiction_result)

    return response
```

### 5.3 Test Case Version Management

```python
# In backend/evaluation/dataset_manager.py
async def update_test_case(test_case_id: str, updates: dict, updated_by: str):
    current = await get_active_version(test_case_id)

    # Determine if version increment needed
    version_fields_changed = [
        k for k in updates.keys()
        if k in VERSION_INCREMENTING_FIELDS
    ]

    if version_fields_changed:
        # Deprecate current version
        await deprecate_version(current, reason=f"Superseded by v{current.version + 1}")

        # Create new version
        new_case = current.model_copy(update={
            **updates,
            "version": current.version + 1,
            "created_at": datetime.utcnow(),
            "created_by": updated_by,
            "deprecated_at": None,
            "is_stale": False,
        })
        await insert_test_case(new_case)
    else:
        # Metadata-only update, no version increment
        await update_metadata(test_case_id, updates)
```

---

## 6. CLI Commands

| Command | Description |
|---|---|
| `quellexctl eval run --dataset <id> --strategies <ids>` | Run evaluation suite |
| `quellexctl eval leaderboard --dataset <id>` | Show current leaderboard |
| `quellexctl judge calibrate --profile <id> --model <model>` | Run calibration protocol |
| `quellexctl judge recalibrate --profile <id>` | Trigger recalibration |
| `quellexctl judge status --profile <id>` | Show calibration status |
| `quellexctl dataset list-stale` | Show stale test cases |
| `quellexctl dataset assign-curator --dataset <id> --user <email>` | Assign curator |
| `quellexctl dataset version-history --case <id>` | Show version history |
| `quellexctl contradiction test --session <id>` | Test contradiction detection on session |

---

## 7. MongoDB Collections

| Collection | Purpose |
|---|---|
| `strategy_test_cases` | Active and historical test case versions |
| `evaluation_results` | All composite evaluation results |
| `judge_calibrations` | Calibration gate results per profile/model |
| `contradiction_logs` | Detected contradictions for analysis |
| `stale_case_reports` | Nightly stale case detection reports |

### Indexes

```javascript
// strategy_test_cases
{ "id": 1, "version": -1 }                    // Latest version lookup
{ "dataset_id": 1, "deprecated_at": 1 }       // Active cases per dataset
{ "is_stale": 1, "deprecated_at": 1 }         // Stale case queries
{ "expected_source_ids": 1 }                   // Stale detection on doc delete

// evaluation_results
{ "test_case_id": 1, "test_case_version": 1, "strategy_id": 1 }  // Leaderboard
{ "run_id": 1 }                                // Run lookup
{ "dataset_id": 1, "created_at": -1 }         // Recent results per dataset

// contradiction_logs
{ "session_id": 1, "current_turn": 1 }        // Session lookup
{ "checked_at": -1 }                          // Recent contradictions
```

---

## 8. Acceptance Criteria

- [ ] `CompositeScoreCalculator` produces identical results to worked examples in this spec
- [ ] Fatal gate correctly zeros composite for all defined fatal failure types
- [ ] Privacy gate correctly zeros composite when cloud judge used on local_only tenant
- [ ] Latency score interpolates linearly between target and hard limit
- [ ] Judge variance detection triggers warning and mean/median fallback at threshold
- [ ] Calibration gate rejects judge model when any dimension correlation < 0.85
- [ ] Test case version auto-increments only for semantic field changes
- [ ] Stale test cases are excluded from automated evaluation runs
- [ ] Contradiction detection activates only for turn > 1 in multi-turn sessions
- [ ] Contradiction detection respects tenant opt-out configuration
- [ ] Contradiction check respects 3000ms circuit breaker timeout
- [ ] All Pydantic models validate correctly with example data
- [ ] CLI commands exist for calibration, stale detection, and version history
