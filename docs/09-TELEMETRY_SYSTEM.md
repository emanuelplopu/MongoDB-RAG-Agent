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
