# Blueprint 05 — Overnight Exploration Scheduler

## 1. Objective

Add a schedulable exploration system that automatically tests strategy variants overnight and reports which prompt-solving patterns are best for each tenant/domain/capability/hardware profile.

This should run without frontend containers and should support CLI control.

---

## 2. Core use cases

1. **Nightly regression**  
   Run known datasets against active production strategies. Detect regressions.

2. **Nightly exploration**  
   Generate or select candidate strategy variants. Run them on datasets. Rank winners.

3. **Hardware profiling**  
   Measure real local model performance, context sensitivity, model load time, and resource pressure.

4. **Promotion candidate generation**  
   If a candidate beats current default by a threshold, mark it as promotable.

5. **Smoke test before demo**  
   Run quick test suite before customer demo/offline deployment.

---

## 3. Scheduler architecture

```text
CLI / API
  -> strategy_schedules collection
  -> StrategyScheduler daemon / worker
       -> creates experiment jobs
       -> StrategyExperimentRunner
            -> StrategyRunner
            -> EvaluationRunner
            -> RuntimeProfiler
       -> Results persisted
       -> Report generated
```

---

## 4. Schedule model

```python
class StrategySchedule(BaseModel):
    id: str
    name: str
    enabled: bool = True
    tenant: str
    profile_key: Optional[str]
    active_matter_id: Optional[str]
    schedule_type: Literal["cron", "window", "manual"]
    cron: Optional[str]
    timezone: str = "Europe/Vienna"
    allowed_window: Optional[str] = "22:00-06:00"

    mode: Literal["regression", "exploration", "profiling", "smoke"]
    datasets: list[str]
    strategy_ids: list[str] = []
    candidate_generator_id: Optional[str] = None
    scoring_profile: str

    resource_limits: ResourceLimits
    stop_conditions: StopConditions
    report_config: ReportConfig
    created_by: str
    created_at: datetime
```

---

## 5. Resource limits

```python
class ResourceLimits(BaseModel):
    max_parallel_runs: int = 1
    max_runtime_minutes: int = 480
    max_llm_calls: Optional[int] = None
    max_cost_eur: Optional[float] = None
    max_gpu_utilization_pct: Optional[int] = 90
    max_cpu_utilization_pct: Optional[int] = 85
    max_ram_usage_pct: Optional[int] = 90
    pause_if_interactive_users: bool = True
    pause_if_backend_chat_active: bool = True
```

For offline/local installs, default to conservative limits.

---

## 6. Exploration modes

### 6.1 Grid exploration

Run all combinations from a defined mutation grid.

Good for early development and small datasets.

```yaml
mutation_grid:
  retrieval_policy.top_k: [6, 10, 14]
  retrieval_policy.query_variants: [1, 2, 3]
  evidence_policy.enabled: [true, false]
  validation_policy.min_citation_coverage: [0.75, 0.85, 0.95]
  model_roles.synthesize: [worker_fast, orchestrator_compact]
```

### 6.2 Bandit exploration

Use multi-armed bandit to allocate more runs to promising strategies.

Good when candidate count is high and overnight time is limited.

```text
initially run each candidate on 3 cases
then repeatedly select candidate with best UCB score
stop at runtime/budget limit
```

### 6.3 Evolutionary exploration

Generate strategy mutations from top performers.

Good after baseline strategies exist.

Mutations:

- reduce context budget
- switch synthesis model
- add/remove evidence cards
- add/remove reranker
- change answer contract strictness
- alter query expansion count
- add validation/refinement node

### 6.4 Regression-only mode

Run production strategies only. No mutation.

Fatal if:

- score drops > 10% against previous baseline
- forbidden source leak appears
- citation coverage drops below threshold
- latency exceeds hard limit on more than N cases

---

## 7. Candidate generator

```text
backend/evaluation/candidate_generator.py
```

```python
class StrategyCandidateGenerator:
    async def generate(
        self,
        base_strategy_id: str,
        mutation_space: MutationSpace,
        count: int,
        constraints: CandidateConstraints,
    ) -> list[StrategySpec]:
        ...
```

### Candidate naming

```text
{base_id}__cand_{YYYYMMDD}_{short_hash}
```

### Candidate safety validation

Reject candidate if:

- it violates tenant privacy policy
- it allows web for private legal matter without explicit permission
- it disables citations where citations are mandatory
- it exceeds max budget
- it references unavailable model roles

---

## 8. Experiment job lifecycle

```text
scheduled -> queued -> running -> completed | failed | cancelled | paused
```

```python
class StrategyExperimentJob(BaseModel):
    id: str
    schedule_id: Optional[str]
    experiment_id: str
    status: str
    priority: int
    tenant: str
    profile_key: Optional[str]
    datasets: list[str]
    strategy_ids: list[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    progress: dict
    error: Optional[str]
```

---

## 9. Scheduler daemon

```text
backend/workers/strategy_scheduler_worker.py
```

Responsibilities:

1. poll schedules
2. check window/timezone/resource limits
3. create experiment jobs
4. run or enqueue jobs
5. pause/resume based on resource limits
6. generate reports
7. mark promotion candidates

Pseudo-flow:

```python
while True:
    schedules = await repo.get_due_schedules(now)
    for schedule in schedules:
        if not schedule.enabled:
            continue
        if not within_allowed_window(schedule):
            continue
        if not resources_available(schedule.resource_limits):
            continue
        job = await create_experiment_job(schedule)
        await experiment_runner.run(job)
    await asyncio.sleep(poll_interval)
```

---

## 10. Nightly report

Generate a Markdown and JSON report after every schedule.

```text
reports/strategy_runs/2026-05-17/overnight_legal_core.md
reports/strategy_runs/2026-05-17/overnight_legal_core.json
```

Report sections:

1. Summary
2. Winning strategies by capability
3. Regressions
4. Fatal failures
5. Latency/cost/resource table
6. Retrieval quality table
7. Citation quality table
8. Recommended promotions
9. Recommended deprecations
10. Test cases needing better gold labels

---

## 11. Promotion workflow

A strategy can be promoted only if:

```yaml
min_runs: 30
min_composite_score: 0.82
beats_current_default_by: 0.05
max_fatal_failures: 0
min_citation_coverage: 0.85
latency_p95_below_ms: 60000
privacy_policy_pass: true
manual_approval_required: true
```

Promotion states:

```text
candidate -> recommended -> approved -> canary -> default -> deprecated
```

---

## 12. CLI examples

```bash
# Run nightly legal exploration once
quellexctl schedule run-now overnight-legal-core

# Add schedule
quellexctl schedule add \
  --name overnight-legal-core \
  --mode exploration \
  --tenant quellex \
  --profile rag_test_law \
  --datasets quellex_legal_core_v1,retrieval_regression_v1 \
  --base-strategy legal_hearing_questions__fast_evidence__v1 \
  --generator grid_legal_fast \
  --cron "0 22 * * *" \
  --window "22:00-06:00" \
  --timezone Europe/Vienna

# Start scheduler daemon without frontend
quellexctl schedule daemon --backend-url http://localhost:11001

# Run exploration directly without persistent schedule
quellexctl experiment run \
  --mode exploration \
  --dataset quellex_legal_core_v1 \
  --strategies legal_hearing_questions__fast_evidence__v1,legal_hearing_questions__deep_orchestrated__v1 \
  --profile rag_test_law \
  --out reports/manual_run.md
```

---

## 13. Acceptance criteria

- A schedule can be created, listed, run-now, paused, and deleted via CLI.
- Scheduler can run without frontend container.
- Scheduler respects allowed time window and resource limits.
- Nightly report is generated in Markdown and JSON.
- Candidate strategies are generated, validated, executed, scored, and ranked.
- Promotion recommendations require explicit thresholds and are not automatic production defaults unless configured.
