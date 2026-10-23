# Telemetry System

## Overview

Production telemetry system that captures complete LLM interaction traces for development, debugging, and product improvement. Supports dual-mode PII protection for alpha/beta testing with trusted partners.

All telemetry operations are non-blocking and failure-tolerant — telemetry never crashes or slows user requests.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEMETRY_ENABLED` | `true` | Master switch for telemetry collection |
| `TELEMETRY_MODE` | `dev` | Operating mode: `dev` / `beta` / `production` / `disabled` |
| `TELEMETRY_PII_MODE` | `both` | PII handling: `both` / `protected_only` / `raw_only` / `disabled` |
| `TELEMETRY_RETENTION_DAYS` | `90` | Days to retain JSONL files before auto-deletion |
| `TELEMETRY_STORAGE_PATH` | `data/telemetry` | Base storage directory for JSONL output |
| `TELEMETRY_PII_MARKERS` | `true` | Wrap PII replacements with `[PII:TYPE]...[/PII]` markers |

Settings are defined in `backend/core/config.py` within `BackendSettings` and loaded at startup.

## PII Pseudonymization

### Triple-Engine Architecture

The pseudonymizer (`backend/services/pii_pseudonymizer.py`) uses an ensemble approach with graceful degradation:

1. **Regex Engine** (always active)
   - Austrian/German patterns: AT IBAN (`AT\d{2}...`), +43 phone numbers, `straße`/`gasse`/`weg`/`platz` addresses
   - Email detection via standard pattern
   - Confidence: 0.90 (regex), 0.75 (address)

2. **spaCy Engine** (optional — requires `spacy` + `de_core_news_lg` model)
   - German NER: PER → PERSON, ORG → COMPANY, LOC/GPE → ADDRESS
   - Confidence: 0.75
   - Falls back gracefully if not installed

3. **Presidio Engine** (optional — requires `presidio-analyzer`)
   - Microsoft Presidio with German NLP engine (`de_core_news_lg`)
   - Entities: PERSON, EMAIL_ADDRESS, PHONE_NUMBER, IBAN_CODE, LOCATION, ORGANIZATION
   - Supported languages: German (`de`), English (`en`)
   - Falls back gracefully if not installed

### Span Merging

When multiple engines detect overlapping PII, spans are merged by preferring higher confidence and longer spans. This prevents double-replacement and ensures the best detection wins.

### Entity Registry

- **Per-session consistency**: Same detected text always resolves to the same alias within a session
- **Deterministic alias generation**: `Person_A`, `Company_B`, `Email_C` pattern (letters A-Z, then A2, B2...)
- **Variant tracking**: Multiple text forms can map to the same entity
- **Entity types**: `PERSON`, `EMAIL`, `PHONE`, `ADDRESS`, `COMPANY`, `IBAN`
- **Session cleanup**: Registry is cleared via `cleanup_session()` when sessions end

### PII Markers

When `TELEMETRY_PII_MARKERS=true`, replacements are wrapped with type tags:

```
[PII:PERSON]Person_A[/PII] sent an email to [PII:EMAIL]Email_A[/PII]
```

This makes redactions visible in the telemetry viewer's comparison mode, showing exactly what was replaced and its entity type.

## Dual-Mode Storage

### Protected Mode (`data/telemetry/YYYY-MM-DD.jsonl`)

- All text fields pseudonymized via the triple-engine pipeline
- User IDs hashed to `user_XXXXX` format
- Safe to share, analyze, and store long-term
- PII markers show what was redacted (when enabled)

### Raw Mode (`data/telemetry/raw/YYYY-MM-DD.jsonl`)

- No pseudonymization applied
- Contains real names, emails, addresses, content
- For trusted partner debugging only
- `.gitignore`'d — never committed to version control
- **WARNING: Contains unprotected PII**

### Mode Behavior Matrix

| PII Mode | Protected File | Raw File |
|----------|:--------------:|:--------:|
| `both` | Written | Written |
| `protected_only` | Written | Skipped |
| `raw_only` | Skipped | Written |
| `disabled` | Skipped | Skipped |

Both file types use async write locks (`asyncio.Lock`) for thread-safe concurrent writes.

## Data Model (`TelemetryRecord`)

Defined in `backend/models/telemetry.py`.

### Metadata

| Field | Type | Description |
|-------|------|-------------|
| `record_id` | `str` (UUID) | Unique record identifier |
| `timestamp` | `datetime` | UTC timestamp of interaction |
| `session_id` | `str` | Chat session identifier |
| `user_id` | `str` | Pseudonymized user identifier |
| `tenant` | `str` | Tenant: `recallhub` or `quellex` |
| `app_version` | `str` | Application version string |

### Request / Response

| Field | Type | Description |
|-------|------|-------------|
| `prompt_original` | `str?` | Pseudonymized original (dev mode only) |
| `prompt_pseudonymized` | `str` | Pseudonymized user prompt |
| `prompt_tokens` | `int` | Input token count |
| `prompt_language` | `str` | Language code (default: `de`) |
| `response_pseudonymized` | `str` | Pseudonymized agent response |
| `response_tokens` | `int` | Output token count |
| `response_latency_ms` | `int` | Total response latency |
| `model_used` | `str` | Primary LLM model identifier |

### Full Capture (every LLM interaction)

| Field | Type | Description |
|-------|------|-------------|
| `llm_calls` | `List[LLMCall]` | Every LLM invocation: phase, model, provider, full prompt/response, tokens, latency, finish_reason, cold_start, repetition_detected |
| `search_operations` | `List[SearchOperation]` | Every search: query, type (vector/text/hybrid), sources, results per source, RRF status, top scores, full chunks |
| `tool_executions` | `List[ToolExecution]` | Every worker task: task_type, input_query, output_text, duration, tokens, sources_searched |
| `phase_metrics` | `List[PhaseMetrics]` | Each orchestrator phase: duration, tokens, input/output size, success |

### Execution Context

| Field | Type | Description |
|-------|------|-------------|
| `agent_mode` | `str` | `thinking`, `fast`, or `auto` |
| `orchestrator_model` | `str` | Model used for orchestration |
| `worker_model` | `str` | Model used for worker tasks |
| `orchestrator_provider` | `str` | Provider (openai, google, ollama, etc.) |
| `worker_provider` | `str` | Provider for workers |
| `orchestrator_duration_ms` | `int` | Orchestrator phase total time |
| `worker_duration_ms` | `int` | Worker execution total time |
| `total_duration_ms` | `int` | End-to-end duration |
| `tokens_per_model` | `dict` | Token breakdown by model name |
| `early_exit_triggered` | `bool` | Whether evaluation confidence >= 0.80 |
| `early_exit_confidence` | `float?` | Confidence score if early exit |
| `total_sources_found` | `int` | Raw source count before dedup |
| `deduplicated_sources` | `int` | Source count after deduplication |

## Admin API Endpoints

All endpoints require admin authentication (`require_admin` dependency).
Router prefix: `/api/v1/admin/telemetry`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/status` | Current config and pseudonymizer engine status |
| `POST` | `/config` | Update config at runtime (no restart needed) |
| `GET` | `/files` | List available JSONL files with metadata |
| `GET` | `/records` | Paginated records by date (`?date=YYYY-MM-DD&source=protected&offset=0&limit=50`) |
| `GET` | `/records/{record_id}` | Single record by ID (`?source=protected|raw`) |
| `POST` | `/search` | Filter by session_id, model, date range, min_latency, query_text |
| `GET` | `/stats` | Aggregated statistics (`?range=7d|30d|all`) |

### Runtime Config Update

The `POST /config` endpoint accepts partial updates:

```json
{
  "enabled": true,
  "mode": "beta",
  "pii_mode": "protected_only",
  "retention_days": 30
}
```

Changes take effect immediately without service restart.

### Stats Response

Returns aggregated metrics over the requested time range:

- `records_count`: Total interaction records
- `avg_latency_ms`: Average response latency
- `total_tokens`: Sum of all tokens consumed
- `error_rate`: Fraction of records with errors
- `model_distribution`: Record count per model

## Integration Points

### Coordinator (`backend/agent/coordinator.py`)

The `_emit_telemetry()` method fires after each agent interaction completes:

1. Extracts LLM calls from orchestrator and worker pool
2. Collects search operations and tool execution records
3. Builds phase metrics from orchestrator trace steps
4. Computes per-model token breakdown
5. Detects early exit conditions (confidence >= 0.80, decision = "sufficient")
6. Emits via `asyncio.create_task()` — fully fire-and-forget

### Design Principles

- **Fire-and-forget**: Telemetry never blocks user responses (`asyncio.create_task`)
- **Graceful degradation**: All telemetry errors are caught and logged at debug level, never propagated
- **Non-blocking writes**: Async locks protect file I/O without blocking the event loop
- **Service initialization**: Telemetry service is attached to `app.state.telemetry` during FastAPI lifespan

## File Rotation

- Files older than `TELEMETRY_RETENTION_DAYS` are automatically deleted
- Both `data/telemetry/*.jsonl` and `data/telemetry/raw/*.jsonl` are cleaned
- Rotation runs at service startup via `rotate_old_files()`
- Files are identified by their `YYYY-MM-DD` stem — non-date filenames are skipped

## Host Telemetry History (host agent, activity events, analysis)

Records what the machine did and what the application was doing at the same time, so slow or
failing ingestion, chat latency and hardware limits can be traced back to their cause. Everything
is superadmin-only and lives in its own MongoDB database (`TELEMETRY_DATABASE`, default
`recallhub_telemetry`), independent of the active profile database and shared by all tenants on a host.

```
Windows host                                   Docker (Linux containers)
┌──────────────────────────────┐               ┌───────────────────────────────────┐
│ RecallHub host agent (.NET)  │  every 5 s    │ API (backend-quellex/-recallhub)   │
│ perf counters, RAPL, nvidia- │──────────────▶│  activity recorder ──┐            │
│ smi, Docker Engine pipe,     │ host_samples  │ ingestion worker     │            │
│ Ollama, /health, LHM+PawnIO  │◀──────────────│  activity recorder ──┤            │
└──────────────────────────────┘ activity_     └──────────────────────┼────────────┘
                                 current                              ▼
                      MongoDB recallhub_telemetry: host_samples · activity_events · activity_current
```

### Why a host agent

The API runs in a Linux container. On Docker Desktop its `psutil` sees the WSL2 VM (its CPU, its
RAM, its uptime), no GPU is attached to it, and CPU temperature/power are not reachable at all.
The **host agent** (`installer/host-agent`, .NET 10, Windows service) reads the real host instead.
The machine-stats widget shows its latest sample (badge **Host**) and falls back to the container
view (badge **VM view**, with a caveat) when no sample is younger than `HOST_AGENT_STALE_SECONDS`.

| Figure | Source in the agent | Notes |
|--------|---------------------|-------|
| CPU %, per core, clock | `\Processor Information\% Processor Utility` (Task Manager's value), `% Processor Performance` × base frequency | clamped to 0–100 |
| RAM, commit | `GlobalMemoryStatusEx` | bytes |
| Drives, disk and network I/O | `DriveInfo`, `\PhysicalDisk(_Total)\*`, `\Network Interface(*)\*` | virtual vEthernet/WSL adapters excluded |
| CPU package / cores / iGPU / DRAM power | `\Energy Meter(RAPL_*)\Power` (Windows RAPL counters, no driver) | DRAM omitted where the platform reports 0 |
| GPU util, VRAM, power, temperatures, clocks, throttle reasons | `nvidia-smi` on the host (field-set detection as in `resources.py`) | `gpu_headroom_c` is the distance to the slowdown limit, not a temperature |
| Per-container CPU (cores), memory, block/net I/O, restarts, health | Docker Engine API over `\\.\pipe\dockerDesktopLinuxEngine` (`one-shot` stats, CPU from consecutive ticks) | 1.0 core = one full core |
| Ollama models | `GET /api/ps` on the host | |
| Backend latency | `GET /health` per tenant (keep-alive; a slow answer = blocked event loop) | |
| **CPU package / core temperature, board temps, fans, NVMe temps** | **LibreHardwareMonitorLib 0.9.6 with the signed PawnIO driver** (opt-in) | needs PawnIO **and** an elevated agent (the service runs as LocalSystem); otherwise `sensors.status` is `driver_missing` / `not_elevated` and the figures are absent |
| What was running | `activity_current` (written by the backends) | job, phase, step, file, progress, chat activity |

ACPI thermal zones are not used for CPU temperature: on this hardware the only zone is a board
sensor (~28 °C). LibreHardwareMonitor ≥ 0.9.5 replaced the WinRing0 driver (flagged by Microsoft
Defender) with PawnIO, which is installed separately.

**Drive temperatures.** LibreHardwareMonitor 0.9.6 reports an NVMe drive's thresholds as
temperature sensors too ("Warning Temperature", "Critical Temperature", next to "Composite
Temperature" and "Temperature #1"–"#8"); SATA drives have a single "Temperature". Per drive the agent
takes "Composite Temperature", else "Temperature", else the hottest "Temperature #N" /
"Temperature Sensor N", and never a name containing Warning, Critical, Limit, Threshold, Max or Min,
nor an unknown name. The composite wins even when a single sensor (often the controller) reads
hotter: it is the value the drive throttles on and the one Windows (`Get-StorageReliabilityCounter`)
and SMART tools show. A drive with nothing usable has no `sensors.storage` entry. Agents before
2026-10-18 took the highest value, i.e. the warning threshold (constant 84–87 °C on Crucial P310
drives that were at 42–43 °C); ignore `storage_max_c` in samples older than the agent update.

### Installing the agent

```powershell
installer\scripts\Install-HostAgent.ps1                                   # service, no CPU temperature
installer\scripts\Install-HostAgent.ps1 -EnableHardwareSensors -InstallPawnIO   # + CPU temperatures (kernel driver)
installer\scripts\Install-HostAgent.ps1 -Mode Task                        # logon task instead of a service
installer\scripts\Uninstall-HostAgent.ps1 [-RemoveConfig] [-RemovePawnIO]
```

`installer\build.ps1` publishes the agent to `output\host-agent` (self-contained single file).
The install script self-elevates, copies it to `%ProgramFiles%\RecallHub\HostAgent`, writes
`%ProgramData%\RecallHub\HostAgent\appsettings.json` (Mongo URI, database, tenant health URLs,
sensors), registers the service `RecallHubHostAgent` (delayed auto-start, restart on failure) and
checks the agent's event-log messages. If the service cannot reach Docker Desktop's engine pipe as
LocalSystem it says so; re-run with `-Mode Task` (runs elevated at the user's logon). Offline
installs pass `-PawnIOInstaller <PawnIO_setup.exe>` instead of using winget. Verify any time with
`RecallHubHostAgent.exe --once` (prints one sample, writes nothing) or `--dry-run`. For sensor
questions, `RecallHubHostAgent.exe --sensors` from an administrator prompt lists every
LibreHardwareMonitor device and sensor (type, name, value), marks which drive temperatures the agent
uses or ignores, and prints the derived `temp` / `sensors` fields; it reads no configuration and
writes nothing (the service can keep running). The tray app shows "Host agent: running / stopped /
not installed".

Configuration precedence: `appsettings.json` next to the exe < the ProgramData override <
`RECALLHUB_AGENT_*` environment variables (e.g. `RECALLHUB_AGENT_Agent__IntervalSeconds=10`) <
command line (`--Agent:IntervalSeconds=10`). While MongoDB is down the agent buffers up to
`BufferMaxSamples` (default 720 = 1 h) in memory.

### `host_samples` (time-series, schema 1)

`timeField` `ts`, `metaField` `meta` (`host`, `source: host-agent`, `agent_version`, `schema`),
granularity seconds, TTL `HOST_SAMPLES_TTL_DAYS` (30). Fields (all optional): `interval_s`,
`sample_ms`, `uptime_s`, `cpu{pct, per_core[], freq_mhz, logical}`,
`mem{used_bytes, total_bytes, pct, commit_used_bytes, commit_limit_bytes}`,
`disks[{path, used_bytes, total_bytes, pct, primary}]`, `disk_io{read_bps, write_bps, busy_pct}`,
`net{rx_bps, tx_bps}`, `power{cpu_pkg_w, cpu_cores_w, cpu_gfx_w, dram_w, gpu_w}`,
`temp{cpu_pkg_c, cpu_core_max_c, cpu_core_avg_c, board_max_c, storage_max_c, gpu_c, gpu_mem_c, gpu_headroom_c}`,
`gpu{name, util_pct, mem_util_pct, vram_used_bytes, vram_total_bytes, sm_mhz, mem_mhz, fan_pct, pstate, power_limit_w, reasons[]}`,
`containers[{name, cpu_cores, mem_bytes, mem_limit_bytes, blk_read_bytes, blk_write_bytes, net_rx_bytes, net_tx_bytes, restart_count, state, started_at, health}]`,
`ollama{models[{name, size_bytes, vram_bytes, ctx}]}`, `health[{name, ms, ok, status}]`,
`activity[{tenant, source, job_id, job_status, phase, step, current_file, processed_files, total_files, chat_active}]`,
`sensors{status, source, elevated, fans[], board[], storage[]}`, `errors{probe: message}`.

### Activity events (`activity_events`, time-series)

`metaField` `meta{kind, source (api|worker), tenant, job_id}`, TTL `ACTIVITY_EVENTS_TTL_DAYS` (90),
indexes `meta.job_id+ts` and `meta.kind+ts`. Written by `ActivityEventRecorder`
(`backend/services/activity_events.py`, one per process, batched every second, retried, bounded):

| Kind | Emitted by | Fields |
|------|-----------|--------|
| `job.status` | API job runner (`routers/ingestion.py`) and ingestion worker | `status` (running, paused, completed, failed, stopped, cancelled, interrupted), `reason`, summary counts |
| `job.phase` | same | `phase` (initializing, discovering, filtering, cleaning, processing, finalizing) |
| `file.start` / `file.end` | the pipeline via `src/ingestion/telemetry_hooks.py` | `file{path, name, ext, size_bytes}`; end: `outcome` (ok, empty, error, cancelled), `duration_ms`, `steps{hash_ms, convert_ms, ocr_repair_ms, chunk_ms, embed_ms, save_ms}`, `chunks`, `pages`, `chars`, `error_type`, `error` |
| `chat.request` | agent coordinator | `session_id`, `trace_id`, `mode`, `strategy`, `model`, `duration_ms`, `orchestrator_ms`, `worker_ms`, `phases[]`, `outcome` — no message text |

`activity_current` holds one document per `<tenant>:<source>` with the live job/phase/step/file and
`chat_last_active_at`; the agent stamps it onto every sample. Events contain internal file paths:
treat the telemetry database like the document database (same access, TTL-limited).

**Tenants.** Host samples describe the machine and are shown to every tenant. Activity is tenant
data: `meta.tenant` (and `tenant` in `activity_current`) is the tenant the job or chat belongs to.
A backend records everything under its `TENANT_ID` and writes `tenant` onto every
`ingestion_jobs` document it saves (`TENANT_FIELD` in `backend/core/ingestion_jobs.py`). The
ingestion worker serves every tenant: its own label is `shared` (or its `TENANT_ID` when a
deployment pins it to one tenant), its live-state document is `shared:worker`, and a job's events
and live state carry the `tenant` recorded on the job (`shared` when the job has none). Whoever
queues a job for the worker must set that field. The API reads activity through a store scoped to
its tenant (`TelemetryStore(tenant_scope=...)`): history, job lists, job reports, anomalies and
the widget's "what is running" lines show only its own and `shared` activity, and another
tenant's job id answers 404. `quellexctl telemetry` is an operator tool on the database and reads
every tenant.
`TELEMETRY_HISTORY_ENABLED=false` turns recording and the history API off (the widget then always
shows the container view).

### Analysis, API, CLI and UI

`backend/services/telemetry_analysis.py` correlates both streams (aggregations run in MongoDB):

* **History**: downsampled host metrics (bucket chosen for ~360 points), per-container CPU/memory,
  finished files and chat requests per bucket, ingestion phase intervals (pauses reconstructed;
  a job silent for 10 min without a final status is closed at its last event).
* **Job report**: phases with host and container resource use, per-step p50/p95/max/share, file
  types (ms per page), slowest, failed and never-finished files, throughput, anomalies.
* **Anomaly rules** (`THRESHOLDS`): memory pressure (RAM ≥ 90 %, critical ≥ 97 %), CPU saturated
  (≥ 95 % for ≥ 30 s), GPU thermal throttling vs power cap, GPU ≥ 85 °C, CPU ≥ 95 °C, backend down
  (critical) or slow `/health` ≥ 2 s (blocked event loop), container ≥ 90 % of its memory limit,
  container restarts, agent sampling gaps, and per file: failed (by error type), cancelled
  (timeouts/stops), unfinished (`file.start` without `file.end`: crash or hang, critical), slow
  (≥ 3× the median of the same type and ≥ 30 s), empty (no chunks).

| Surface | Path |
|---------|------|
| API (superadmin; 503 when disabled) | `GET /api/v1/telemetry/history?hours=6` · `/jobs` · `/jobs/{id}` · `/jobs/{id}/report.html` · `/anomalies?hours=24` |
| Widget | `GET /api/v1/system/host-stats` (latest sample or container fallback) |
| UI | System → Telemetry History (`/system/telemetry`): range presets, synced charts with phase bands, anomalies, table view, jobs and job report with HTML download |
| CLI | `quellexctl telemetry jobs \| job --job ID \| anomalies [--job ID] \| report --job ID --out report.html` (talks to MongoDB directly) |

The HTML job report reuses the benchmark renderer (`synthetic_corpus/telemetry_html.py`) by
converting agent samples to its sample layout and appends the findings; it is a single file to
attach to a bug report.

### Settings

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEMETRY_HISTORY_ENABLED` | `true` | record activity events and read agent samples |
| `TELEMETRY_DATABASE` | `recallhub_telemetry` | database of the three collections (agent `Agent:Database` must match) |
| `HOST_SAMPLES_TTL_DAYS` | `30` | retention of host samples (applied with `collMod` at startup) |
| `ACTIVITY_EVENTS_TTL_DAYS` | `90` | retention of activity events |
| `HOST_AGENT_STALE_SECONDS` | `20` | older agent samples count as offline for the widget |
| `HOST_STATS_DISK_PATH` | empty | disk reported by the container fallback (first existing `DOCUMENT_ROOTS` entry by default) |

Size: about 17,000 samples a day at 5 s; time-series compression keeps that to a few MB per day.

### Tests

* Agent: `dotnet test installer/host-agent.Tests` (parsers, Docker CPU math, RAPL mapping, sensor
  mapping, activity stamping; a MongoDB round trip when `RECALLHUB_TEST_MONGO_URI` is set).
* Backend: `backend/tests/test_host_stats.py`, `test_telemetry_store.py`, `test_activity_events.py`,
  `test_telemetry_history.py`; against a throwaway MongoDB (never the stack's):
  `backend/tests/integration/test_telemetry_store_mongo.py` and `test_telemetry_analysis_mongo.py`
  with `RECALLHUB_TEST_MONGO_URI=mongodb://127.0.0.1:27999/?directConnection=true`.
* Frontend: `HostStatsWidget.test.tsx`, `components/telemetry/TimeSeriesChart.test.tsx`,
  `pages/TelemetryHistoryPage.test.tsx`.
