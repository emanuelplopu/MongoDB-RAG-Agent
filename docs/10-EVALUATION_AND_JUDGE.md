# Strategy OS — Evaluation, Scoring & Judging

Last reviewed: 2026-05-18

This blueprint documents the **evaluation system** that scores strategy outputs end-to-end: the 7-dimension scoring model, the latency / fatal / privacy gates, the LLM-judge calibration protocol, the contradiction detector, the test-case manager, and the API/CLI for running evaluations and comparing strategies on a leaderboard.

The evaluation system is what turns Strategy OS from "we changed the prompt and it feels better" into "we changed the prompt and the composite score moved from 0.71 to 0.78 on the legal dataset, with a 95% judge-correlation calibration on the scoring profile." It is also the gate behind `PromotionManager` — see [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md).

---

## 1. Composite Score Formula

The composite score is the single number that ranks two strategies against each other on a dataset.

```
composite = (Σ score_i × weight_i) × latency_score × fatal_gate × privacy_gate
```

Where:

- `score_i` ∈ `[0.0, 1.0]` for each of the 7 dimensions (see §2).
- `weight_i` is the dimension weight from the active `ScoringProfile` (sums to 1.0).
- `latency_score` is a piecewise linear function — `1.0` at or below `target_ms`, `0.0` at or above `hard_limit_ms`, linear in between.
- `fatal_gate` ∈ `{0, 1}` — multiplies the score to zero if any safety violation is recorded.
- `privacy_gate` ∈ `{0, 1}` — multiplies the score to zero if a `local_only` spec leaked to a cloud LLM.

The result is clamped to `[0.0, 1.0]`.

---

## 2. Seven Quality Dimensions

| `DimensionId` | Default weight | What it measures |
|---|---|---|
| `groundedness` | 0.25 | Every factual claim is backed by retrieved evidence |
| `citation_quality` | 0.20 | Citations point to the correct chunk and quote span |
| `directness` | 0.10 | Answer addresses the actual question without filler |
| `completeness` | 0.15 | All required sections from the answer contract are present and substantive |
| `format_adherence` | 0.15 | Output matches the answer contract (sections, table schema, enums, citation granularity) |
| `domain_value` | 0.10 | Domain-specific usefulness (e.g. correct legal terminology, actionable hearing questions) |
| `conciseness` | 0.05 | No restating of the question, no padding |

Weights live on `ScoringProfile.weights` (`backend/evaluation/models.py`). A capability may override the profile via `BusinessCapability.scoring_profile` so that, e.g., the `legal_hearing_questions` capability emphasizes `format_adherence` and `domain_value` over `conciseness`.

---

## 3. `EvaluationTestCase` & `EvaluationRun`

```python
class EvaluationTestCase(BaseModel):
    id: str
    version: int
    user_prompt: str
    expected_source_ids: list[str]          # canonical chunks the answer should cite
    expected_topics: list[str]              # topics the answer should cover
    scoring_profile: str = "default"
    dataset_id: str = "default"
    tags: list[str]
    deprecated_at: datetime | None
    is_stale: bool
    created_at: datetime
    updated_at: datetime
```

Test cases are versioned: when you mutate the prompt or expected sources, you bump `version` rather than overwriting. The `EvaluationRun` document records `test_case_id` + `test_case_version` so historical runs remain interpretable after the case evolves.

```python
class EvaluationRun(BaseModel):
    run_id: str
    test_case_id: str
    test_case_version: int
    strategy_id: str
    result: CompositeResult
    judge_model: str | None
    judge_variance: float | None    # max spread across repeated judge calls
    duration_ms: float
    created_at: datetime
```

Persistence: `evaluation_results` collection. Indexed on `(strategy_id, created_at)` and `(dataset_id, strategy_id)`.

---

## 4. `EvaluationRunner`

`backend/evaluation/runner.py` exposes the runner:

```python
class EvaluationRunner:
    async def run_evaluation(
        self,
        strategy_id: str,
        dataset_id: str,
        judge_model: str | None = None,
        repeats: int = 1,
    ) -> list[EvaluationRun]: ...
```

For each test case in `dataset_id`:

1. Resolve the `StrategySpec` for `strategy_id` (latest active version).
2. Build a `BusinessContext` for the prompt (tenant, profile, language).
3. Call `StrategyRunner.run(spec, business_context, query=test_case.user_prompt)` to produce a `StrategyRunResult`.
4. Score the result via the configured judge model + `CompositeScoreCalculator`. When `repeats > 1`, the judge is called multiple times and `judge_variance` = max spread across calls (used to gate noisy judges).
5. Detect contradictions (§7) and inject the `ContradictionResult` into the run record.
6. Persist an `EvaluationRun` document to `evaluation_results`.

---

## 5. `CompositeScoreCalculator`

`backend/evaluation/composite_scorer.py` implements the formula in §1:

```python
def calculate(
    self,
    dimension_scores: list[DimensionScore],
    latency_ms: float,
    fatal_gate: int = 1,
    privacy_gate: int = 1,
    latency_config: LatencyConfig | None = None,
) -> CompositeResult: ...
```

`LatencyConfig.target_ms` and `LatencyConfig.hard_limit_ms` come from the scoring profile. The piecewise-linear function clamps below target to `1.0` and above hard limit to `0.0`. Setting `fatal_gate=0` or `privacy_gate=0` produces `composite=0.0` regardless of other dimensions — a strategy that hallucinates a citation or leaks a private query to a cloud LLM cannot have a positive composite.

---

## 6. Judge Calibration

The judge is itself an LLM, so its outputs are noisy. `JudgeCalibrator` (`backend/evaluation/judge_calibrator.py`) controls when a (judge model, scoring profile) pair is allowed to produce evaluation scores.

```python
class JudgeCalibration(BaseModel):
    calibration_id: str
    profile_id: str
    judge_model: str
    dimension_correlations: dict[str, float]   # dim_id -> Pearson r vs. human gold
    overall_correlation: float
    gate_status: CalibrationStatus             # passed | rejected | pending
    seed_case_count: int
    variance_mean: float                        # mean variance across repeated runs
    calibrated_at: datetime
```

### Protocol

1. Curate a small set of **seed test cases** (typically 20–50) with **human-graded** dimension scores. These live as `EvaluationTestCase` entries tagged `seed`.
2. Run the judge model on each seed case `N` times (default `N=3`) to measure judge variance.
3. Compute Pearson correlation between judge means and human gold per dimension; combine into `overall_correlation`.
4. Apply the gate:
   - `passed` if `overall_correlation >= 0.75` AND `variance_mean <= 0.10` AND no dimension correlation below `0.50`.
   - `rejected` if `overall_correlation < 0.5` OR `variance_mean > 0.20`.
   - `pending` otherwise (operator review).
5. Persist as a `JudgeCalibration` record. `EvaluationRunner` refuses to use a judge model whose latest calibration is not `passed` for the active profile.

Re-calibration is required on judge model upgrades and on scoring profile weight changes.

---

## 7. Contradiction Detection

`ContradictionDetector` (`backend/evaluation/contradiction_detector.py`) detects contradictions both **between** responses (when comparing strategies on the same prompt) and **within** a single response.

```python
class Contradiction(BaseModel):
    claim_a: ClaimExtraction
    claim_b: ClaimExtraction
    conflict_type: str                  # semantic | factual | temporal
    confidence: float
    attribution: ContradictionAttribution  # strategy_related | data_related | model_variance
```

The detector:

1. Extracts atomic claims from each response using a claim-extraction prompt.
2. Pairwise compares claims with the LLM judge.
3. Classifies each contradiction by `conflict_type` and attributes it:
   - `strategy_related` — the same data yielded different outputs due to strategy differences.
   - `data_related` — the underlying retrieved chunks disagree.
   - `model_variance` — the answer flips on repeated runs of the same strategy.

`ContradictionResult` is persisted as part of `EvaluationRun.result.metadata`.

---

## 8. Test Case Manager

`TestCaseManager` (`backend/evaluation/test_case_manager.py`) is the CRUD interface for `evaluation_test_cases` (in-memory or Mongo).

```python
class TestCaseManager:
    async def create(self, case: EvaluationTestCase) -> EvaluationTestCase: ...
    async def get(self, case_id: str, version: int | None = None) -> EvaluationTestCase: ...
    async def update(self, case: EvaluationTestCase) -> EvaluationTestCase: ...     # bumps version
    async def delete(self, case_id: str) -> None: ...                                # soft delete + is_stale
    async def list_dataset(self, dataset_id: str) -> list[EvaluationTestCase]: ...
    async def tag(self, case_id: str, tags: list[str]) -> EvaluationTestCase: ...
```

Soft-delete semantics: `is_stale = True` + `deprecated_at` is set. Historical runs against the stale version remain queryable.

---

## 9. REST API (`backend/routers/evaluation.py`, prefix `/api/v1/evaluation`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/run` | Trigger an evaluation run for a `strategy_id` over a `dataset_id` |
| `POST` | `/compare` | Run two strategies on the same dataset and return side-by-side composites + contradictions |
| `GET` | `/leaderboard` | Ranked strategies for a dataset (composite descending, with margin) |
| `GET` | `/results/{strategy_id}` | All `EvaluationRun` documents for a strategy (cursor-paginated) |
| `POST` | `/test-cases` | Create a test case |
| `GET` | `/test-cases` | List test cases (filter by `dataset_id`, `tags`) |
| `DELETE` | `/test-cases/{test_case_id}` | Soft-delete |

The router takes care of auth (admin-only by default) and rate limiting.

---

## 10. CLI (`quellexctl eval ...`, `quellexctl experiment ...`)

`backend/cli/eval_commands.py` exposes the evaluation surface as Typer commands:

| Command | Purpose |
|---|---|
| `quellexctl eval run --strategy <id> --dataset <id>` | Run an evaluation; print a per-dimension table |
| `quellexctl eval leaderboard --dataset <id>` | Print leaderboard for a dataset |
| `quellexctl experiment run --strategies <id1,id2,...> --dataset <id>` | Compare multiple strategies (also exposed under `experiment` — see [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md)) |
| `quellexctl experiment report --job <job_id>` | Fetch the result of a completed experiment job |

All commands accept `--format json` for machine-readable output.

---

## 11. Building a Reliable Dataset

A test case is only useful if it has unambiguous expected behavior. Rules of thumb when authoring:

- **One concept per case.** Don't bundle two questions into a single prompt.
- **Use real source ids in `expected_source_ids`.** The retriever-fidelity metrics depend on it.
- **Tag aggressively.** `tags: ["legal_hearing_questions", "regression", "edge_case_german_dialect"]` lets you slice the leaderboard.
- **Seed before scaling.** Hand-grade 20–50 seed cases before adding 500 LLM-generated cases. Use seeds for judge calibration; reserve the rest for routine evaluation.
- **Version aggressively.** When you tweak the prompt or expected behavior, bump `version`. Old runs remain interpretable.

---

## 12. Read Next

- For specs and capability binding that the runner uses → [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md)
- For the scheduler that runs evaluations overnight and produces nightly reports → [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md)
- For how the runtime accumulates the state that gets scored here → [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md)
