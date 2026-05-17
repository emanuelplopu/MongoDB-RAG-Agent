# Blueprint 06 — Headless CLI and Backend-Only Runner

## 1. Objective

Make strategy testing, profiling, scheduling, and telemetry analysis available without frontend containers.

This is required for:

- overnight exploration
- CI/CD regression testing
- offline deployments
- local Kanzlei machines
- developer workflows
- benchmark runs without UI

---

## 2. CLI package

Create:

```text
tools/quellexctl/
  pyproject.toml
  quellexctl/
    __init__.py
    cli.py
    commands/
      strategy.py
      experiment.py
      schedule.py
      telemetry.py
      dataset.py
      profiler.py
      run.py
    client.py
    config.py
    output.py
```

Use Python + Typer or argparse. Typer is preferred if already acceptable in the dependency stack.

---

## 3. Execution modes

### 3.1 API mode

CLI talks to running backend API.

```bash
quellexctl --backend-url http://localhost:11001 strategy list
```

Best for normal local/offline installs.

### 3.2 In-container mode

CLI runs inside backend container and calls internal service classes.

```bash
docker compose exec backend-quellex quellexctl experiment run --dataset quellex_legal_core_v1
```

Best when host Python is not configured.

### 3.3 Module/direct mode

CLI imports backend modules and uses local `.env` and MongoDB.

```bash
quellexctl --mode direct experiment run --dataset quellex_legal_core_v1
```

Best for development and CI.

### 3.4 Offline fixture mode

CLI runs against JSONL telemetry or static fixture data without backend.

```bash
quellexctl --mode fixtures telemetry analyze --input data/telemetry/2026-05-17.jsonl
```

Best for analysis and reporting.

---

## 4. Backend-only Docker profile

Add compose profile:

```yaml
profiles:
  backend-only:
    services:
      - mongodb
      - backend-quellex
      - strategy-scheduler-worker
```

Example:

```bash
docker compose --profile quellex --profile backend-only up -d mongodb backend-quellex strategy-scheduler-worker
```

Do not start frontend.

---

## 5. CLI command tree

```text
quellexctl
  status
  strategy
    list
    get <id>
    validate <file-or-id>
    diff <id-a> <id-b>
    clone <id> --new-id ...
    activate <id>
    promote <id>
    export <id>
    import <file>
  dataset
    list
    get <id>
    validate <file-or-id>
    import <file>
    export <id>
    create-from-telemetry
  experiment
    run
    list
    get <run-id>
    compare <run-a> <run-b>
    leaderboard
    report <run-id>
  schedule
    list
    add
    get <id>
    enable <id>
    disable <id>
    run-now <id>
    daemon
    logs <id>
  telemetry
    files
    search
    show <record-id>
    replay <record-id>
    stats
    summarize
  profiler
    hardware
    runtime
    model-test
    strategy-profile
  run
    prompt
    test-case
```

---

## 6. Key commands

### 6.1 Run one prompt through one strategy

```bash
quellexctl run prompt \
  --tenant quellex \
  --profile rag_test_law \
  --strategy legal_hearing_questions__fast_evidence__v1 \
  --prompt "ich suche ergänzungsfragen von einen immobilienprozess" \
  --out response.md \
  --trace trace.json
```

### 6.2 Run dataset against strategies

```bash
quellexctl experiment run \
  --tenant quellex \
  --profile rag_test_law \
  --dataset quellex_legal_core_v1 \
  --strategies legal_hearing_questions__fast_evidence__v1,legacy_legal_orchestrator_v1 \
  --judge local \
  --report reports/legal_core.md
```

### 6.3 Start overnight daemon

```bash
quellexctl schedule daemon \
  --tenant quellex \
  --backend-url http://localhost:11001 \
  --poll-interval 60 \
  --log-file logs/strategy_scheduler.log
```

### 6.4 Replay telemetry record

```bash
quellexctl telemetry replay <record_id> \
  --strategy legal_hearing_questions__fast_evidence__v1 \
  --compare-with-original \
  --out replay_report.md
```

---

## 7. Authentication

Support:

1. Admin bearer token
2. API key
3. local dev bypass only when `ENV=development`
4. in-container service token from environment

Environment variables:

```text
QUELLEXCTL_BACKEND_URL=http://localhost:11001
QUELLEXCTL_API_KEY=...
QUELLEXCTL_TOKEN=...
QUELLEXCTL_DEFAULT_TENANT=quellex
QUELLEXCTL_DEFAULT_PROFILE=rag_test_law
```

---

## 8. CLI output formats

All commands should support:

```bash
--format table|json|yaml|markdown
--out path
```

Default:

- human-readable table for interactive terminal
- JSON for `--format json`
- Markdown for reports

---

## 9. API endpoints required for CLI

Add or extend endpoints:

```text
POST /api/v1/strategy-runs/run-prompt
POST /api/v1/evaluation/run-dataset
GET  /api/v1/evaluation/runs
GET  /api/v1/evaluation/runs/{id}
GET  /api/v1/evaluation/leaderboard
POST /api/v1/strategy-schedules
GET  /api/v1/strategy-schedules
POST /api/v1/strategy-schedules/{id}/run-now
POST /api/v1/strategy-schedules/{id}/toggle
GET  /api/v1/strategy-schedules/{id}/logs
POST /api/v1/telemetry/replay/{record_id}
```

---

## 10. Acceptance criteria

- CLI can run with only backend + MongoDB running.
- CLI can run strategy tests without frontend container.
- CLI can create and trigger schedules.
- CLI can output JSON and Markdown reports.
- CLI can replay a telemetry record.
- CLI failures return non-zero exit codes and machine-readable error messages.
- CLI can be included in offline deployment package.
