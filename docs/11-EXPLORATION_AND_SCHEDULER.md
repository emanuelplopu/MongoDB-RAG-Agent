# Strategy OS — Exploration & Scheduler

Last reviewed: 2026-05-18

This blueprint documents the **exploration system** that searches the space of strategy specs to find improvements, and the **overnight scheduler** that runs that exploration on a cron with proper resource gating, pause/stop semantics, and nightly reporting.

The pieces:

- **Five exploration modes** (`grid`, `regression`, `smoke`, `bandit`, `evolutionary`) materialize a candidate spec list for one experiment job.
- **`CandidateGenerator`** + **`EvolutionaryMutator`** produce concrete `StrategySpec` variants.
- **`StrategyExperimentRunner`** executes a job across the (strategy × dataset) matrix with resource limits.
- **`SchedulerDaemon`** dispatches scheduled jobs, pauses when interactive users or resource pressure appear, and stops on budget/leader/circuit-breaker signals.
- **`NightlyReportGenerator`** aggregates the previous day's runs into a Markdown report.

For evaluation scoring of each run, see [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md). For specs and promotion that absorbs winners back into production, see [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md).

---

## 1. Exploration Modes

`backend/agent/strategy/exploration_modes.py` ships five modes. Each implements `ExplorationMode.materialize_candidates(job, spec_store) -> list[StrategySpec]`.

| Mode | Materializer | Typical use |
|---|---|---|
| `grid` | `GridMode` — wraps `CandidateGenerator` with a YAML `mutation_grid` block (cartesian product of config knobs) | Enumerated A/B/C/... over a parameter sweep |
| `regression` | `RegressionMode` — re-runs the production strategies for the job's tenant + capability without mutation, with fatal-fail triggers (default thresholds in `DEFAULT_REGRESSION_THRESHOLDS`) | Nightly safety net: prove no regression on the canonical dataset |
| `smoke` | `SmokeMode` — single strategy, small deterministic dataset sample | Sanity check after deploying a new spec |
| `bandit` | `BanditMode` — UCB1 over an existing candidate pool, with persistent arm state in `strategy_bandit_state` | Online optimization across nightly runs — converges to the best arm faster than grid |
| `evolutionary` | `EvolutionaryMode` — population-based mutation across generations, state in `strategy_evolution_state` | Open-ended search when the parameter space is large or the right mutation is unknown |

The dispatcher is `ModeRegistry`. Unknown modes raise `UnknownExplorationModeError`. Each mode hands the runner a flat list of `StrategySpec` candidates and (for stateful modes) updates its state store between generations.

### 1.1 Grid mode

`GridMode` consumes a YAML grid like:

```yaml
mutation_grid:
  retrieval_policy.top_k: [10, 20, 30]
  retrieval_policy.reranker: ["llm", "cross_encoder", "none"]
  evidence_policy.max_cards: [10, 20]
```

The `CandidateGenerator` walks the cartesian product (`3 × 3 × 2 = 18 candidates`) and clones the base spec with each combination applied. `MutationGridYAMLError` covers malformed YAML.

### 1.2 Regression mode

`RegressionMode` is the safety net. It runs every currently-`active` spec for the job's tenant + capability against the regression dataset (typically the `seed` test cases used for judge calibration). Default fatal triggers (from `DEFAULT_REGRESSION_THRESHOLDS`):

| Trigger | Default | Effect |
|---|---|---|
| Composite drop vs. last passing run | > 0.05 | Flag as regression in nightly report |
| Fatal-gate failures | > 0 | Block re-promotion until fixed |
| Privacy-gate failures | > 0 | Block re-promotion + page operator |
| p95 latency increase | > 25% | Flag for review |

### 1.3 Smoke mode

`SmokeMode` runs one strategy against a tiny sample (default 5 cases). Used as a pre-flight check before launching a full grid/bandit job.

### 1.4 Bandit mode

`BanditMode` implements **UCB1**:

- Initial warm-up: `DEFAULT_BANDIT_WARMUP_PULLS_PER_ARM` (default 3) pulls per arm.
- Subsequent allocation: pick `arm = argmax(mean + c × sqrt(ln(N) / n_i))` where `c = DEFAULT_BANDIT_EXPLORATION_CONSTANT` (default `sqrt(2)`), `N` is total pulls, `n_i` is pulls of arm `i`.
- State persists in `strategy_bandit_state` keyed by `(experiment_id, arm_id)`.

When a new bandit job re-uses an existing pool, prior arm statistics are loaded so convergence is preserved across nights.

### 1.5 Evolutionary mode

`EvolutionaryMode` evolves a population across generations:

- Start with a seed population (the current active spec + a few hand-picked variants).
- Each generation: score the population, take top `DEFAULT_EVOLUTION_TOP_K` (default 4), apply `EvolutionaryMutator` to spawn children.
- `EvolutionaryMutator` has 10 mutation operators (reduce_context_budget, switch_synthesis_model, add/remove_evidence_cards_node, add/remove_reranker_node, change_contract_strictness, alter_query_expansion_count, add_validation_node, add_refinement_node). Each mutation is bounded by spec validity rules.
- Stop after `DEFAULT_EVOLUTION_GENERATIONS` (default 5) or when no child beats the parent within the regression threshold.

State (per-generation population, mutation history, scores) lives in `strategy_evolution_state`.

---

## 2. `StrategyExperimentJob` & `ExperimentJobStore`

A job (`backend/agent/strategy/experiment_job_models.py`) is the durable lifecycle record for one "schedule fired" event or one on-demand operator request.

```python
class StrategyExperimentJob(BaseModel):
    id: str
    schedule_id: str | None              # None for ad-hoc
    experiment_id: str                   # logical id; multiple jobs may share
    status: JobStatus                    # see state machine below
    priority: int
    tenant: str
    profile_key: str | None
    mode: Literal["regression", "exploration", "profiling", "smoke", "grid", "bandit", "evolutionary"]
    datasets: list[str]
    strategy_ids: list[str]
    candidate_generator_id: str | None   # for exploration mode
    scoring_profile: str | None
    started_at: datetime | None
    completed_at: datetime | None
    progress: JobProgress
    error: str | None
    trace_ids: list[str]                 # RunTraceDoc.trace_id values produced
    result_summary: dict | None          # populated on completion
    resource_limits: ResourceLimits | None
    created_at: datetime
    updated_at: datetime
```

### State machine

```
scheduled ─► queued     ─► cancelled
queued    ─► running    ─► cancelled  ─► paused
running   ─► completed  ─► failed     ─► cancelled  ─► paused
paused    ─► queued     ─► cancelled
completed, failed, cancelled  →  terminal
```

`StrategyExperimentJob.allowed_transitions(from_status)` is the single source of truth. `InvalidJobTransitionError` is raised by the store on illegal transitions.

### Storage

`MongoExperimentJobStore` persists to `strategy_experiment_jobs`. Indexes on `(status, priority, created_at)` for dispatcher queue scans and `(experiment_id, created_at)` for history lookups.

---

## 3. `StrategyExperimentRunner`

`StrategyExperimentRunner.run_job(job, callbacks)` executes a job:

1. Transition `scheduled → queued → running`. Stamp `started_at`.
2. Resolve `ExplorationMode` via `ModeRegistry[job.mode]`.
3. `materialize_candidates(job, spec_store)` → list of `StrategySpec` candidates.
4. Expand the matrix: `for strategy in candidates: for dataset in job.datasets: for case in dataset.cases:` — populate `JobProgress.total_runs`.
5. For each run:
   1. Check `ResourceLimits` (max concurrent runs, GPU/CPU/RAM caps). If exceeded, raise `ExperimentResourceExceeded` and yield to the daemon's pause/throttle handler.
   2. Build `BusinessContext` and call `StrategyRunner.run(...)`.
   3. Score the result via `EvaluationRunner` (writes `evaluation_results`).
   4. Append the `trace_id` to `job.trace_ids` and increment `progress.completed_runs` (or `failed_runs`).
   5. Invoke any registered callbacks (e.g. live UI progress).
6. Aggregate `result_summary`: top winner, p50/p95 latency, fatal/privacy gate failure rates, cost. Transition `running → completed`.

Failure modes:

- `InvalidJobStateError` — job was cancelled or transitioned externally mid-run.
- `ExperimentResourceExceeded` — re-queued or paused depending on `job.resource_limits` policy.
- Per-run exceptions are caught, counted into `failed_runs`, and recorded as `trace_ids` with `status=error`.

---

## 4. `SchedulerDaemon`

`backend/scheduler/scheduler_daemon.py` is the long-running loop. One instance per Strategy-OS-enabled backend worker; only one may dispatch at a time (enforced by a Mongo-backed lease).

### Main loop (tick interval = `strategy_scheduler_tick_interval_seconds`, default 60s)

1. **Load due schedules.** `SchedulerStore.list_due(now)` reads `strategy_schedules` and returns schedules whose `next_run_at <= now`, are not `paused`, and pass the circuit-breaker check.
2. **Pause evaluation.** `PauseConditionEvaluator.evaluate()` checks:
   - **Interactive users.** Was the last user chat message in the last `interactive_cooldown_minutes` (default 5)? `ChatActivityTracker` reads from `runtime_signals`.
   - **Resource pressure.** Current CPU/RAM/GPU above the schedule's `SchedulerBudget` caps (default 60/50/70%)? `ResourceSnapshotCollector` writes the latest snapshot to `runtime_signals`.
   - **Manual pause.** Operator paused via API/CLI.
   If any trigger fires, the daemon defers all dispatch for this tick and records `PauseDecision`. If pause persists beyond `max_pause_minutes` (default 120), the schedule moves to `DEFERRED` and is skipped for the night.
3. **Resource gating.** `ResourceContentionManager` enforces a process-wide concurrency cap (max experiment runners in parallel). New jobs that don't fit wait.
4. **Ollama residency.** `OllamaResidencyManager` ensures the model the schedule needs is loaded before launch, and detects thrashing (`load_count_10min`). Thrashing pauses the schedule.
5. **Circuit breaker.** `MongoDBCircuitBreaker` wraps every Mongo write. After `failure_threshold` writes fail (default 5), it transitions `CLOSED → OPEN`. While `OPEN`, the daemon refuses new dispatches. After a recovery window it transitions to `HALF_OPEN` and probes.
6. **Dispatch.** For each non-paused, non-gated, due schedule:
   1. Create a `StrategyExperimentJob` and enqueue it.
   2. Launch a `StrategyExperimentRunner` task.
   3. Update the schedule's `last_run_at`, `next_run_at` (via `ScheduleTimezoneResolver.next_fire_time(cron, tz)`), and `consecutive_failures` counter.
7. **Stop evaluation.** `StopConditionEvaluator.evaluate(run_state, stop_conditions)` checks per-job stop gates (see §5).
8. **Nightly report.** If we are at the configured nightly hour (default 03:00 local, configurable via `strategy_nightly_report_*` settings) and a report hasn't fired today, run `NightlyReportGenerator.generate_and_store(...)`.

### Persistence

- `SchedulerStore` is `MongoSchedulerStore` over `strategy_schedules`. Schedule runtime state (`paused`, `paused_reason`, `last_run_at`, `last_run_status`, `next_run_at`, `consecutive_failures`, `circuit_breaker_state`) survives restarts.
- Per-run results land in `scheduler_results` and aggregates in `scheduler_reports`.

---

## 5. Pause and Stop Conditions

### `PauseConfig` (per schedule)

| Field | Default | Meaning |
|---|---|---|
| `max_pause_minutes` | 120 | Cumulative pause before `DEFERRED` for the night |
| `pause_if_interactive_users` | `True` | Pause when chat activity within cooldown |
| `pause_if_resource_pressure` | `True` | Pause when CPU/RAM/GPU above the schedule's caps |
| `interactive_cooldown_minutes` | 5 | How long after the last chat message to remain paused |

### `StopConditions` (per schedule)

| Field | Default | Effect |
|---|---|---|
| `max_runtime_minutes` | 360 | Hard wall-clock cap |
| `max_llm_calls` | 500 | LLM call budget |
| `max_cost_usd` | 50.0 | Cloud-spend budget |
| `max_consecutive_failures` | 5 | Stop if too many candidates fail in a row |
| `stop_on_fatal_failure_rate` | 0.5 | Stop if >50% of completed runs hit the fatal gate |
| `stop_on_leader_found` | `True` | Stop early when a clear leader emerges |
| `leader_margin_pct` | 10.0 | Leader must beat the runner-up by 10% composite |
| `min_candidates_completed` | 3 | Activate leader detection only after this many runs |

All stop reasons are persisted on `SchedulerRunState.stop_reason` (`StopReason` enum) and surfaced in the nightly report.

---

## 6. Nightly Report

`NightlyReportGenerator.generate_and_store(start_dt, end_dt)` builds a Markdown report covering the lookback window (`strategy_nightly_report_lookback_hours`, default 24h):

- **Top movers.** Strategies whose composite changed by more than `strategy_nightly_report_regression_threshold` (default 0.05) vs. the previous lookback.
- **Regressions.** Active strategies with a fatal-gate or privacy-gate failure.
- **Candidate winners.** New candidates that beat the current leader by `leader_margin_pct`.
- **Resource summary.** Total LLM calls, total cost USD, pause minutes, deferred schedules.
- **Per-schedule recap.** Status, stop reason, candidates evaluated, leader, ranking, warnings.

Inputs are read from `strategy_runs`, `evaluation_results`, `scheduler_results`, `scheduler_reports`, and `runtime_model_profiles`. The Markdown is written to `data/reports/strategy/<date>.md` (override via `strategy_nightly_report_dir`) and a record is stored in `strategy_run_reports`.

The report fires from inside the scheduler daemon's main loop at `strategy_nightly_report_hour_local` in `strategy_nightly_report_timezone` (DST-safe via `ScheduleTimezoneResolver`).

---

## 7. REST API (`backend/routers/scheduler.py`, prefix `/api/v1/scheduler`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/schedules` | List all schedules |
| `POST` | `/schedules` | Create a schedule |
| `GET` | `/schedules/{schedule_id}` | Fetch one |
| `DELETE` | `/schedules/{schedule_id}` | Delete |
| `POST` | `/schedules/{schedule_id}/run-now` | Force-fire immediately (subject to pause/circuit-breaker) |
| `GET` | `/status` | Daemon status: running, paused, current job, contention |
| `POST` | `/pause` | Pause the daemon (operator) |
| `POST` | `/resume` | Resume |
| `GET` | `/reports` | List nightly reports |

---

## 8. CLI

`backend/cli/phase6_commands.py` registers two Typer apps:

### `quellexctl schedule …`

| Command | Purpose |
|---|---|
| `quellexctl schedule list` | List schedules with status, next_run_at, last_run_status |
| `quellexctl schedule get <id>` | Full schedule details + runtime state |
| `quellexctl schedule add --name --cron --mode --tenant --datasets --strategies ...` | Create a schedule |
| `quellexctl schedule run-now <id>` | Trigger immediately |
| `quellexctl schedule pause <id> --reason "..."` | Pause one schedule |
| `quellexctl schedule resume <id>` | Resume |
| `quellexctl schedule daemon` | Run the scheduler daemon in the foreground (useful for ops) |

### `quellexctl experiment …`

| Command | Purpose |
|---|---|
| `quellexctl experiment run --mode <grid\|regression\|smoke\|bandit\|evolutionary> --strategies ... --dataset ...` | Submit an ad-hoc job |
| `quellexctl experiment list` | List jobs with status/progress |
| `quellexctl experiment get <job_id>` | Full job details, including `progress`, `trace_ids`, `result_summary` |
| `quellexctl experiment report <job_id>` | Render a final report for a completed job |

### `quellexctl profiler …`

| Command | Purpose |
|---|---|
| `quellexctl profiler model-test --model <id> --tokens N` | One-off latency / cost benchmark of a model role |
| `quellexctl profiler strategy-profile --strategy <id> --query "..."` | End-to-end profile: per-node latency, tokens, cost breakdown |

Profiler output writes into `runtime_model_profiles` so the planner has fresh data when sizing new specs.

---

## 9. Authoring a Schedule

```yaml
# Submit via POST /api/v1/scheduler/schedules
name: "Quellex Legal — Nightly Bandit"
cron: "0 22 * * 1-5"            # 22:00 Mon–Fri
timezone: "Europe/Vienna"
allowed_window_start: "22:00"
allowed_window_end: "06:00"

dataset_id: "quellex_legal_v1"
candidate_strategy_ids: ["deep_orchestrated_v1", "fast_evidence_v1"]

mode: "bandit"
tenant: "quellex"
profile_key: "rag_test_law"
candidate_generator_id: "legal_pool_v1"

pause_config:
  max_pause_minutes: 120
  pause_if_interactive_users: true
  pause_if_resource_pressure: true
  interactive_cooldown_minutes: 5

stop_conditions:
  max_runtime_minutes: 360
  max_llm_calls: 500
  max_cost_usd: 50.0
  stop_on_leader_found: true
  leader_margin_pct: 10.0
  min_candidates_completed: 3

budget:
  max_cpu_pct: 60.0
  max_ram_pct: 50.0
  max_gpu_pct: 70.0
```

---

## 10. Operational Playbook

| Symptom | Likely cause | First check |
|---|---|---|
| Schedule never fires | `paused=true`, or circuit breaker `OPEN`, or `next_run_at` in the future due to past failure | `GET /api/v1/scheduler/schedules/{id}` and `GET /api/v1/scheduler/status` |
| Daemon constantly paused | Interactive activity in cooldown window, or resource pressure above caps | `runtime_signals` recent docs; tighten `SchedulerBudget` if hardware-bound |
| Bandit not converging | Insufficient `min_candidates_completed`, or `leader_margin_pct` too aggressive | Inspect `strategy_bandit_state` rows for the experiment |
| Evolutionary stuck | Population diversity collapsed, or every mutation produces invalid specs | Inspect `strategy_evolution_state`; lower `top_k`, broaden mutation set |
| Nightly report missing | `strategy_nightly_report_enabled=false`, wrong timezone, or daemon paused at report hour | Check `strategy_run_reports`; verify env vars |
| Circuit breaker stuck OPEN | Persistent Mongo write failures | Check Mongo logs; manually transition via DB if root cause resolved |

---

## 11. Read Next

- For the evaluation that scores each candidate → [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md)
- For specs, capabilities, and promotion of winning candidates → [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md)
- For the runtime the runner invokes per candidate → [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md)
- For Strategy OS-wide setup, settings, and integration points → [07-STRATEGY_OS_OVERVIEW.md](./07-STRATEGY_OS_OVERVIEW.md)
