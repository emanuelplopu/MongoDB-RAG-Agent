# Blueprint 13G — Dependency and Platform Requirements

> **Resolves specification gaps:** ID-1 (new package inventory), ID-2 (MongoDB feature tiers), ID-3 (LLM capability matrix), SC-1 (Ollama platform behaviors), SC-2 (OS/signal handling), SC-3 (scalability limits).

> **Target audience:** DevOps engineers, senior Python developers, and infrastructure leads.

---

## 1. Python Package Requirements for Strategy OS

### 1.1 Already-present packages (DO NOT duplicate)

These packages are already declared in `pyproject.toml` or `backend/requirements*.txt` and satisfy Strategy OS needs as-is:

| Package | Current Constraint | Strategy OS Usage |
|---|---|---|
| `pydantic` | `>=2.10.0` | StrategySpec, EvidenceCard, schedule, and all typed contract validation |
| `pydantic-settings` | `>=2.7.0` | `BackendSettings` extensions for Strategy OS config surface |
| `pyyaml` | `>=6.0.0` | Strategy spec YAML loading (**MUST** use `yaml.safe_load` only) |
| `rich` | `>=13.9.0` | CLI table/progress output for `quellexctl` |
| `litellm` | `>=1.55.0` | Multi-provider LLM dispatch (model roles, capability probing) |
| `httpx` | `>=0.27.0` | Ollama health/residency polling, async HTTP calls |
| `pymongo` | `>=4.10.0` | Async MongoDB access (`AsyncMongoClient`) |
| `motor` | `>=3.6.0` | Legacy async MongoDB (existing code; new code MUST use PyMongo Async) |

### 1.2 New packages required

| Package | Min Version | Required / Optional | Component | Rationale |
|---|---|---|---|---|
| `networkx` | `>=3.0` | **Required** | DAG Execution | Topological sort, cycle detection, level computation for `StrategyGraph` |
| `croniter` | `>=2.0` | **Required** | Cron Parsing | Cron expression parsing with timezone support for `StrategySchedule` |
| `pytz` | `>=2024.1` | **Required** | Timezone | IANA timezone database for schedule windows. Stdlib `zoneinfo` (Python 3.9+) is available but `croniter` requires `pytz` as a transitive dependency, so pin it explicitly. |
| `typer` | `>=0.12` | **Required** | CLI Framework | `quellexctl` CLI construction with auto-complete and rich help |
| `psutil` | `>=5.9` | **Required** | Resource Monitoring | CPU/RAM metrics for `ResourceLimits` enforcement and runtime profiler |
| `pynvml` | `>=11.5` | **Optional** | Resource Monitoring | NVIDIA GPU metrics (VRAM usage, utilization). Gracefully degrade if not installed or no GPU present. |

### 1.3 Proposed `pyproject.toml` addition

```toml
[project.optional-dependencies]
strategy-os = [
    "networkx>=3.0",
    "croniter>=2.0",
    "pytz>=2024.1",
    "typer>=0.12",
    "psutil>=5.9",
]
strategy-os-gpu = [
    "pynvml>=11.5",
]
```

### 1.4 Installation

```bash
# Core Strategy OS dependencies
uv sync --extra strategy-os

# With GPU monitoring support
uv sync --extra strategy-os --extra strategy-os-gpu
```

### 1.5 Docker layer placement

| Layer | File | Packages Added |
|---|---|---|
| `requirements-api.txt` | API layer (fast rebuild) | `networkx`, `croniter`, `pytz`, `typer`, `psutil` |
| `requirements-ml-heavy.txt` | ML layer (slow rebuild) | _None_ — no ML-heavy deps added |
| Runtime optional | Not in image by default | `pynvml` (only for GPU-equipped hosts) |

**Rationale:** All new packages are lightweight (<5 MB combined install) and install in seconds. They belong in the API layer to keep rebuild times under 30 seconds for code changes.

### 1.6 Import guard pattern for optional dependencies

```python
# backend/core/platform.py

def get_gpu_monitor():
    """Return GPU monitor or None if pynvml unavailable."""
    try:
        import pynvml
        pynvml.nvmlInit()
        return pynvml
    except (ImportError, pynvml.NVMLError):
        return None
```

---

## 2. MongoDB Feature Tier Requirements

### 2.1 Hard requirements (Strategy OS will NOT function without these)

| Feature | Minimum | Why Required | Current Infrastructure |
|---|---|---|---|
| **MongoDB Atlas Local 8.0+** or **MongoDB Atlas (cloud)** | `mongodb/mongodb-atlas-local:8.0` | `$vectorSearch` pipeline stage for semantic retrieval nodes | `docker-compose.yml` service `mongodb` uses this image |
| **Replica set mode** | Enabled (Atlas Local provides this automatically) | Multi-document transactions for atomic strategy promotion; change streams for schedule detection | Atlas Local runs as single-node replica set by default |
| **Atlas Vector Search indexes** | `vector_index` on `chunks` collection | All `retrieve` nodes use `$vectorSearch` as the first aggregation stage | Defined in `profiles.yaml` per profile |

### 2.2 Soft requirements (degraded but functional without)

| Feature | Degradation | Fallback |
|---|---|---|
| **Full-text search indexes** (`text_index`) | Hybrid search unavailable; RRF fusion disabled | Basic `$regex` text search on `text` field |
| **Change streams** | Real-time schedule trigger detection unavailable | Polling-based schedule detection every 60 seconds |
| **Atlas Search analyzers** | Advanced linguistic analysis unavailable | Standard MongoDB text indexes with default analyzer |

### 2.3 Feature detection on startup

The Strategy OS MUST perform capability detection during application lifespan startup and expose results through a `PlatformCapabilities` singleton.

```python
from pydantic import BaseModel
from enum import Enum

class FeatureStatus(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

class PlatformCapabilities(BaseModel):
    """Detected platform capabilities, populated at startup."""
    vector_search: FeatureStatus = FeatureStatus.UNAVAILABLE
    replica_set: FeatureStatus = FeatureStatus.UNAVAILABLE
    atlas_search: FeatureStatus = FeatureStatus.UNAVAILABLE
    change_streams: FeatureStatus = FeatureStatus.UNAVAILABLE
    transactions: FeatureStatus = FeatureStatus.UNAVAILABLE
    gpu_available: bool = False
    gpu_vram_gb: float = 0.0
    ollama_reachable: bool = False
    ollama_models: list[str] = []
```

#### Startup validation pseudo-code

```python
async def detect_platform_capabilities(db) -> PlatformCapabilities:
    caps = PlatformCapabilities()

    # 1. Vector Search
    try:
        await db.chunks.aggregate([
            {"$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": [0.0] * 1536,
                "numCandidates": 1,
                "limit": 1,
            }}
        ]).to_list(1)
        caps.vector_search = FeatureStatus.AVAILABLE
    except Exception as e:
        logger.critical(
            "Vector search unavailable: %s. "
            "Vector-based strategies will be DISABLED.", e
        )
        caps.vector_search = FeatureStatus.UNAVAILABLE

    # 2. Replica Set / Transactions
    try:
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                pass  # no-op transaction test
        caps.replica_set = FeatureStatus.AVAILABLE
        caps.transactions = FeatureStatus.AVAILABLE
    except Exception as e:
        logger.warning(
            "Replica set / transactions unavailable: %s. "
            "Atomic promotion disabled; using compensating logic.", e
        )
        caps.replica_set = FeatureStatus.DEGRADED
        caps.transactions = FeatureStatus.UNAVAILABLE

    # 3. Atlas Search (full-text)
    try:
        await db.chunks.aggregate([
            {"$search": {
                "index": "text_index",
                "text": {"query": "test", "path": "text"},
            }},
            {"$limit": 1},
        ]).to_list(1)
        caps.atlas_search = FeatureStatus.AVAILABLE
    except Exception:
        logger.info(
            "Atlas Search unavailable. Falling back to basic text indexes."
        )
        caps.atlas_search = FeatureStatus.DEGRADED

    # 4. Change Streams
    try:
        async with db.strategy_specs.watch(max_await_time_ms=100) as stream:
            pass  # successfully opened
        caps.change_streams = FeatureStatus.AVAILABLE
    except Exception:
        logger.info("Change streams unavailable. Using polling fallback.")
        caps.change_streams = FeatureStatus.DEGRADED

    # 5. GPU detection
    gpu = get_gpu_monitor()
    if gpu:
        caps.gpu_available = True
        handle = gpu.nvmlDeviceGetHandleByIndex(0)
        mem = gpu.nvmlDeviceGetMemoryInfo(handle)
        caps.gpu_vram_gb = round(mem.total / (1024**3), 1)

    # 6. Ollama reachability
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                caps.ollama_reachable = True
                caps.ollama_models = [
                    m["name"] for m in resp.json().get("models", [])
                ]
    except Exception:
        caps.ollama_reachable = False

    return caps
```

### 2.4 Offline / USB deployment requirements

| Requirement | Detail |
|---|---|
| Docker image | `mongodb/mongodb-atlas-local:8.0` MUST be included in the installer package |
| Community MongoDB | **NOT sufficient** — lacks `$vectorSearch` and Atlas Search |
| Image size | ~1.2 GB compressed; ~2.5 GB uncompressed |
| Pre-created indexes | Installer must run `scripts/init-mongodb.js` to create vector and text indexes on first startup |
| Data volume | `./data/mongoDB/db` and `./data/mongoDB/configdb` must be on a drive with ≥10 GB free space |

---

## 3. LLM Capability Matrix

### 3.1 Model role definitions

| Role | Required Capabilities | Optional | Recommended (Cloud) | Recommended (Local) |
|---|---|---|---|---|
| `classifier` | `low_latency`, `text_completion` | — | `gpt-4o-mini` | `ollama/gemma3:4b` |
| `query_expander` | `text_completion` | — | `gpt-4o-mini` | `ollama/gemma3:4b` |
| `reranker` | `text_completion`, `scoring` | — | `gpt-4o-mini` | `ollama/gemma3:4b` |
| `evidence_compressor` | `structured_output`, `json_mode` | — | `gpt-4o` | `ollama/gemma3:12b` |
| `synthesizer_fast` | `text_completion`, `streaming` | — | `gpt-4o-mini`, `gemini-2.0-flash` | `ollama/gemma3:12b` |
| `synthesizer_deep` | `text_completion`, `streaming`, `long_context` | — | `gpt-4o`, `claude-3.5-sonnet` | `ollama/gemma4:26b` |
| `judge` | `json_mode`, `deterministic_seed`, `structured_output` | — | `gpt-4o` | `ollama/gemma3:12b` (limited) |
| `validator` | `text_completion`, `json_mode` | — | `gpt-4o-mini` | `ollama/gemma3:4b` |

### 3.2 Capability definitions

| Capability | Definition | Detection Method |
|---|---|---|
| `text_completion` | Basic text generation | Always assumed available |
| `streaming` | Supports token-by-token SSE streaming | Provider metadata or probe call |
| `json_mode` | Can reliably output valid JSON when instructed | Provider metadata; verify with test prompt |
| `structured_output` | Supports schema-guided generation (e.g., OpenAI `response_format`) | Provider API feature flag |
| `deterministic_seed` | Supports `seed` parameter for reproducible output | Provider documentation check |
| `long_context` | Supports ≥32k token context windows | Model metadata `max_context_tokens` |
| `scoring` | Can output calibrated numerical scores (0.0–1.0) | Validated via profiling test `tiny_classification` |
| `low_latency` | First-token latency <500ms (cloud) or <2000ms (local warm) | Runtime profiler measurement |

### 3.3 Strategy spec validation

On strategy spec registration, the system MUST validate:

1. Every `model_role` referenced by a graph node has a model assignment.
2. The assigned model's declared capabilities are a superset of the role's required capabilities.
3. If `local_only: true` in budgets, all model roles MUST resolve to local providers (`ollama`).
4. If a role references an unavailable model (e.g., not in Ollama model list), emit a WARNING but allow registration (model may be pulled later).

### 3.4 Fallback chain

When the primary model for a role is unavailable at runtime:

```text
1. Check model_capabilities.yaml for alternatives in same tier
2. If same-tier alternative exists and meets required capabilities → use it
3. If no same-tier alternative → check next tier down
4. If no viable alternative → fail the node with error, do NOT silently downgrade
```

### 3.5 `model_capabilities.yaml` reference file

```yaml
# backend/config/model_capabilities.yaml
# Model capability declarations for Strategy OS role assignment.
# This file is loaded by StrategyRegistry at startup.

models:
  # --- Cloud models ---
  gpt-4o:
    provider: openai
    tier: 1
    max_context_tokens: 128000
    capabilities:
      - text_completion
      - streaming
      - json_mode
      - structured_output
      - deterministic_seed
      - long_context
      - scoring
    cost_per_million_input: 2.50
    cost_per_million_output: 10.00

  gpt-4o-mini:
    provider: openai
    tier: 2
    max_context_tokens: 128000
    capabilities:
      - text_completion
      - streaming
      - json_mode
      - structured_output
      - deterministic_seed
      - long_context
      - scoring
      - low_latency
    cost_per_million_input: 0.15
    cost_per_million_output: 0.60

  gemini-2.0-flash:
    provider: google
    tier: 2
    max_context_tokens: 1048576
    capabilities:
      - text_completion
      - streaming
      - json_mode
      - long_context
      - low_latency
    cost_per_million_input: 0.10
    cost_per_million_output: 0.40

  claude-3.5-sonnet:
    provider: anthropic
    tier: 1
    max_context_tokens: 200000
    capabilities:
      - text_completion
      - streaming
      - json_mode
      - long_context
    cost_per_million_input: 3.00
    cost_per_million_output: 15.00

  # --- Local models (Ollama) ---
  ollama/gemma3:4b:
    provider: ollama
    tier: 3
    max_context_tokens: 32000
    capabilities:
      - text_completion
      - streaming
      - low_latency
    local_only: true
    vram_required_gb: 3.0

  ollama/gemma3:12b:
    provider: ollama
    tier: 2
    max_context_tokens: 32000
    capabilities:
      - text_completion
      - streaming
      - json_mode
      - structured_output
      - scoring
    local_only: true
    vram_required_gb: 8.0

  ollama/gemma4:26b:
    provider: ollama
    tier: 1
    max_context_tokens: 128000
    capabilities:
      - text_completion
      - streaming
      - json_mode
      - structured_output
      - long_context
      - scoring
    local_only: true
    vram_required_gb: 18.0

# Fallback chains: ordered list of alternatives per role
fallback_chains:
  classifier:
    - ollama/gemma3:4b
    - gpt-4o-mini
  query_expander:
    - ollama/gemma3:4b
    - gpt-4o-mini
  reranker:
    - ollama/gemma3:4b
    - gpt-4o-mini
  evidence_compressor:
    - ollama/gemma3:12b
    - gpt-4o
  synthesizer_fast:
    - ollama/gemma3:12b
    - gpt-4o-mini
    - gemini-2.0-flash
  synthesizer_deep:
    - ollama/gemma4:26b
    - gpt-4o
    - claude-3.5-sonnet
  judge:
    - ollama/gemma3:12b
    - gpt-4o
  validator:
    - ollama/gemma3:4b
    - gpt-4o-mini
```

---

## 4. Ollama Platform-Specific Behaviors

### 4.1 Model loading characteristics

| Scenario | Latency | Notes |
|---|---|---|
| **Cold start** (model not in memory) | 10–30s (SSD) / 30–60s (HDD) | Depends on model size (4B–26B parameters) |
| **Warm start** (model resident) | <1s | Model already loaded in RAM/VRAM |
| **Concurrent loading** | Queued | Only 1 model loads at a time; subsequent requests wait |
| **Model swap** | 5–15s | Unload current model + load new one |

### 4.2 VRAM management

```text
Model loading priority:
  1. Fit entire model in VRAM → full GPU inference (fastest)
  2. Partial VRAM fit → split layers GPU/CPU (slower, functional)
  3. No VRAM / no GPU → full CPU inference via RAM (slowest)
  4. Insufficient RAM → Ollama returns error (will NOT OOM-kill)
```

| Model | Min VRAM (full GPU) | Min RAM (CPU only) | Recommended |
|---|---|---|---|
| `gemma3:4b` | 3 GB | 6 GB | 6 GB VRAM or 8 GB RAM |
| `gemma3:12b` | 8 GB | 16 GB | 12 GB VRAM or 24 GB RAM |
| `gemma4:26b` | 18 GB | 32 GB | 24 GB VRAM or 48 GB RAM |

### 4.3 Concurrent inference

| Scenario | Behavior |
|---|---|
| **Same model, multiple requests** | Supported — concurrent KV cache allocation up to available memory |
| **Different models** | Must unload one to load another; 5–15s swap penalty |
| **Batch scheduling** | Scheduler SHOULD group all runs using the same model to avoid thrashing |

**Strategy OS recommendation:** The `StrategyScheduler` MUST sort experiment jobs by `model_role` so that all runs requiring the same model execute consecutively before switching to the next model.

### 4.4 Model eviction and keep-alive

| Parameter | Default | Strategy OS Override |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `5m` | Set to `30m` during overnight runs |
| Eviction policy | FIFO — last-used model stays resident; evicted when new model requested | — |
| Multiple models resident | Possible if combined size fits in VRAM+RAM | Strategy OS should not assume this |

**Environment variable injection:**

```yaml
# docker-compose.override.yml addition for Strategy OS
services:
  backend:
    environment:
      - OLLAMA_KEEP_ALIVE_OVERRIDE=30m  # Strategy OS reads this; passes to Ollama via API
```

At runtime, the `OllamaResidencyManager` sets keep-alive per request:

```python
# POST /api/generate or /api/chat
{
    "model": "gemma4:26b",
    "keep_alive": "30m",
    ...
}
```

### 4.5 Health monitoring

| Endpoint | Method | Purpose | Poll Interval |
|---|---|---|---|
| `/api/tags` | `GET` | List available models (pulled to disk) | On startup, then every 5 minutes |
| `/api/ps` | `GET` | Currently loaded models + VRAM/RAM usage | Every 30s during active strategy runs |
| `/api/show` | `POST` | Model metadata (parameters, quantization, context length) | On demand / cache for 1 hour |

#### `OllamaResidencyManager` responsibilities

```python
class OllamaResidencyManager:
    """Monitors Ollama model residency and resource consumption."""

    async def poll_status(self) -> OllamaStatus:
        """Poll /api/ps for loaded models and memory usage."""
        ...

    async def ensure_model_loaded(self, model: str, keep_alive: str = "5m") -> bool:
        """Pre-warm a model by sending a minimal generate request."""
        ...

    async def get_available_models(self) -> list[str]:
        """List models available on disk via /api/tags."""
        ...

    def is_model_resident(self, model: str) -> bool:
        """Check if model is currently loaded from last poll."""
        ...

    def estimate_swap_time(self, from_model: str, to_model: str) -> float:
        """Estimate seconds to swap models based on historical observations."""
        ...
```

---

## 5. Operating System and Platform Requirements

### 5.1 Supported platforms

| Platform | Runtime | Notes |
|---|---|---|
| **Windows 10/11** | Docker Desktop (WSL2 backend) | Primary target for Quellex USB deployments |
| **Linux** (Ubuntu 22.04+, Debian 12+) | Docker CE / Docker Compose V2 | Cloud deployments and CI |
| **macOS** (Apple Silicon) | Docker Desktop | Development only; no GPU passthrough for Ollama in Docker |

### 5.2 Signal handling

| Platform | Signal | Strategy OS Behavior |
|---|---|---|
| **Linux containers** | `SIGTERM` | 30s grace period; flush pending results to MongoDB, persist checkpoint, close DB connections |
| **Windows containers** | Docker stop signal | Use `atexit` handlers + `asyncio` shutdown hooks; same flush behavior |
| **Scheduler daemon** | `SIGTERM` / `SIGINT` | Register `asyncio.get_event_loop().add_signal_handler()` to mark running jobs as `paused`, flush partial results |

#### Shutdown handler pseudo-code

```python
import asyncio
import signal
import atexit

class GracefulShutdown:
    """Handles graceful shutdown for Strategy OS workers."""

    def __init__(self, scheduler: StrategyScheduler):
        self.scheduler = scheduler
        self._shutting_down = False

    def register(self):
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._handle_signal)
            except NotImplementedError:
                # Windows: add_signal_handler not supported
                signal.signal(sig, lambda s, f: self._handle_signal())
        atexit.register(self._sync_cleanup)

    def _handle_signal(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        asyncio.create_task(self._async_cleanup())

    async def _async_cleanup(self):
        logger.info("Graceful shutdown initiated; flushing pending results...")
        await self.scheduler.pause_all_running_jobs()
        await self.scheduler.flush_pending_results()
        await self.scheduler.close_connections()
        logger.info("Shutdown complete.")
```

### 5.3 File system layout

| Path (in container) | Mount Type | Access | Purpose |
|---|---|---|---|
| `/app/config/strategies/` | Bind mount or embedded | **Read-only** | Strategy YAML spec files |
| `/app/config/model_capabilities.yaml` | Bind mount or embedded | **Read-only** | Model capability declarations |
| `/app/data/telemetry/` | Named volume or bind mount | **Read-write** | Telemetry JSONL output |
| `/app/data/backups/` | Bind mount | **Read-write** | Backup artifacts |
| MongoDB collections | N/A (database) | **Read-write** | Checkpoints, results, schedules — NOT filesystem |

**Key constraint:** Strategy execution checkpoints MUST be stored in MongoDB (collection `strategy_checkpoints`), not the filesystem, to ensure portability across container restarts and host changes.

### 5.4 Distributed locks (multi-instance scheduler)

> **MVP scope:** Single scheduler instance. Distributed locks are a post-MVP concern but the design is specified here for forward compatibility.

| Parameter | Value |
|---|---|
| **Lock collection** | `scheduler_locks` |
| **Lock key format** | `schedule:{schedule_id}:{target_date_YYYYMMDD}` |
| **Lock TTL** | `max_runtime_minutes` from schedule config |
| **Pattern** | Check-and-set: `insertOne` with unique index on `lock_key`; TTL index on `expires_at` |
| **Release** | Explicit `deleteOne` on job completion; TTL auto-expires on crash |

```python
async def acquire_schedule_lock(
    db, schedule_id: str, target_date: str, ttl_minutes: int
) -> bool:
    """Attempt to acquire advisory lock for a schedule run.

    Returns True if lock acquired, False if another instance holds it.
    """
    try:
        await db.scheduler_locks.insert_one({
            "lock_key": f"schedule:{schedule_id}:{target_date}",
            "acquired_by": INSTANCE_ID,
            "acquired_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=ttl_minutes),
        })
        return True
    except DuplicateKeyError:
        return False
```

Required indexes on `scheduler_locks`:

```javascript
db.scheduler_locks.createIndex({ "lock_key": 1 }, { unique: true })
db.scheduler_locks.createIndex({ "expires_at": 1 }, { expireAfterSeconds: 0 })
```

---

## 6. Scalability Limits and Performance Constraints

### 6.1 Strategy registry

| Parameter | Limit | Rationale |
|---|---|---|
| **Max active strategies per tenant** | Recommended <100 | Beyond this, selection queries on `(capability_id, status, tenant_scope)` degrade |
| **Registry cache** | In-memory; reload on `strategy_specs` collection change | Use change stream if available; fall back to polling every 60s |
| **Required index** | `{ capability_id: 1, status: 1, tenant_scope: 1 }` | Fast selection queries |
| **Spec size limit** | 256 KB per strategy spec document | Prevents bloated YAML/JSON specs from degrading MongoDB performance |

### 6.2 Maximum DAG depth

| Parameter | Value | Enforcement |
|---|---|---|
| **Hard limit** | 25 nodes per strategy graph | Validated at spec registration; reject with `422 Unprocessable Entity` |
| **Recommended** | 8–12 nodes | Documented guidance for strategy authors |
| **Minimum** | 2 nodes (entry + terminal) | At least an entry and terminal node required |

**Rationale:** Each node adds ~1–30s of execution time depending on type (deterministic nodes ~1s, LLM nodes ~5–30s). A 25-node graph could mean 12+ minutes total execution. Strategies exceeding this must decompose into sub-strategies or use chaining.

**Decomposition guidance:**

```text
If your strategy needs >25 nodes:
  1. Split into a "preparation" sub-strategy and a "synthesis" sub-strategy
  2. Use the strategy_chaining pattern: output of strategy A feeds into strategy B
  3. Persist intermediate state in strategy_checkpoints collection
```

### 6.3 Evidence card limits

| Parameter | Default | Hard Max | Enforcement |
|---|---|---|---|
| `EvidencePolicy.max_cards` | 20 | 50 | Spec validation at registration |
| `max_cards_per_topic` | 4 | 10 | Spec validation at registration |

**Context budget enforcement:**

```text
1. Retrieve node returns N chunks (potentially hundreds)
2. evidence_cards node selects top-N by relevance score (capped at max_cards)
3. Before synthesis: estimate token count per card (~500 tokens each)
4. If total exceeds context_budget_tokens:
   a. Sort cards by relevance score descending
   b. Drop lowest-relevance cards until within budget
   c. Log dropped cards with reason in telemetry
5. Example: 20 cards × ~500 tokens = ~10,000 tokens additional context
```

### 6.4 Evaluation result volume

| Metric | Expected Range | Action |
|---|---|---|
| Results per strategy per dataset | 10–100 | Normal operation |
| Total results after months of overnight exploration | 100k+ | Requires archival strategy |
| Single result document size | ~2–5 KB | Manageable |

**Required indexes on `strategy_evaluation_results`:**

```javascript
db.strategy_evaluation_results.createIndex(
    { strategy_id: 1, dataset_id: 1, created_at: -1 }
)
db.strategy_evaluation_results.createIndex(
    { experiment_id: 1 }
)
db.strategy_evaluation_results.createIndex(
    { test_case_id: 1, created_at: -1 }
)
```

**Archival strategy:**

| Approach | Trigger | Action |
|---|---|---|
| **TTL-based** | Results older than `evaluation_retention_days` (default: 180) | MongoDB TTL index on `created_at` |
| **Manual archival** | Admin CLI command | Export to JSONL, delete from collection |
| **Materialized leaderboard** | After each experiment completes | Pre-compute `$group` aggregation into `strategy_leaderboard` collection |

**Leaderboard materialization:**

```javascript
// Run after each experiment completion
db.strategy_evaluation_results.aggregate([
    { $match: { experiment_id: "exp_..." } },
    { $group: {
        _id: { strategy_id: "$strategy_id", dataset_id: "$dataset_id" },
        avg_composite_score: { $avg: "$composite_score" },
        avg_latency_ms: { $avg: "$latency_ms" },
        avg_citation_coverage: { $avg: "$citation_coverage" },
        total_runs: { $sum: 1 },
        fatal_failure_count: {
            $sum: { $cond: [{ $gt: [{ $size: "$fatal_failures" }, 0] }, 1, 0] }
        },
        last_run_at: { $max: "$created_at" },
    }},
    { $merge: {
        into: "strategy_leaderboard",
        on: "_id",
        whenMatched: "replace",
        whenNotMatched: "insert",
    }},
])
```

### 6.5 Telemetry volume projections

| Scenario | Per-Event Size | Frequency | Daily Volume | Monthly Volume |
|---|---|---|---|---|
| Interactive query | 5–50 KB | 50–200/day | 2.5–10 MB | 75–300 MB |
| Overnight experiment (per strategy run) | 50–500 KB | 10–100/night | 0.5–50 MB | 15 MB–1.5 GB |
| Combined moderate usage | — | — | ~50 MB | ~1.5 GB |
| Combined heavy usage | — | — | ~200 MB | ~5 GB |

**Telemetry rotation policy:**

| Parameter | Value | Source |
|---|---|---|
| `telemetry_retention_days` | 90 (existing) | `backend/core/config.py` |
| `strategy_telemetry_max_size_mb` | 500 (new) | Strategy OS config extension |
| Rotation trigger | Whichever threshold is reached first | JSONL file rotation |
| Archive behavior | Compress rotated files to `.jsonl.gz`, move to `data/telemetry/archive/` | Existing pattern |

### 6.6 Summary of collection-level constraints

| Collection | Max Documents (recommended) | Index Strategy | TTL |
|---|---|---|---|
| `strategy_specs` | <500 per tenant | `(capability_id, status, tenant_scope)` unique on `id` | None (manual lifecycle) |
| `strategy_test_cases` | <10,000 per tenant | `(dataset_id, tenant)`, `(capability_id)` | None |
| `strategy_evaluation_results` | Unlimited (with archival) | `(strategy_id, dataset_id, created_at)`, `(experiment_id)` | 180 days |
| `strategy_experiments` | <10,000 | `(tenant, status)`, `(created_at)` | 365 days |
| `strategy_schedules` | <100 per tenant | `(tenant, enabled)`, `(next_run_at)` | None |
| `runtime_model_profiles` | <100,000 | `(host_id, model, created_at)` | 90 days |
| `strategy_leaderboard` | <5,000 | `(strategy_id, dataset_id)` | Replaced on re-computation |
| `strategy_checkpoints` | <1,000 active | `(strategy_run_id)`, TTL on `expires_at` | 24 hours |
| `scheduler_locks` | <100 | Unique on `lock_key`, TTL on `expires_at` | Auto-expire |

---

## Appendix A: Environment Variables Added by Strategy OS

| Variable | Default | Description |
|---|---|---|
| `STRATEGY_SPECS_DIR` | `/app/config/strategies` | Directory containing strategy YAML specs |
| `MODEL_CAPABILITIES_PATH` | `/app/config/model_capabilities.yaml` | Path to model capabilities declaration file |
| `STRATEGY_REGISTRY_POLL_INTERVAL` | `60` | Seconds between registry reload polls (if change streams unavailable) |
| `SCHEDULER_POLL_INTERVAL` | `30` | Seconds between scheduler due-schedule checks |
| `SCHEDULER_ENABLED` | `false` | Enable the overnight exploration scheduler daemon |
| `OLLAMA_KEEP_ALIVE_OVERRIDE` | `5m` | Override Ollama keep-alive during strategy runs |
| `STRATEGY_MAX_DAG_NODES` | `25` | Hard limit on nodes per strategy graph |
| `STRATEGY_TELEMETRY_MAX_SIZE_MB` | `500` | Max telemetry size before rotation |
| `EVALUATION_RETENTION_DAYS` | `180` | TTL for evaluation results |

## Appendix B: Dependency Version Compatibility Matrix

| Package | Min Version | Max Tested | Python 3.10 | Python 3.11 | Python 3.12 | Notes |
|---|---|---|---|---|---|---|
| `networkx` | 3.0 | 3.4 | Yes | Yes | Yes | No native deps |
| `croniter` | 2.0 | 3.0 | Yes | Yes | Yes | Requires `pytz` |
| `pytz` | 2024.1 | 2025.1 | Yes | Yes | Yes | IANA tz data |
| `typer` | 0.12 | 0.15 | Yes | Yes | Yes | Requires `click>=8.0` |
| `psutil` | 5.9 | 6.1 | Yes | Yes | Yes | C extension; binary wheels available |
| `pynvml` | 11.5 | 12.0 | Yes | Yes | Yes | Requires NVIDIA driver ≥450.x |

## Appendix C: Cross-Reference to Other Blueprints

| Section | Related Blueprint | Link |
|---|---|---|
| DAG execution model | 02 — Strategy Engine V2 | `StrategyGraph`, `StrategyNode`, `StrategyEdge` |
| Model roles | 08 — Runtime Performance | `ModelRoleConfig`, role-based routing |
| Schedule model | 05 — Overnight Exploration Scheduler | `StrategySchedule`, `ResourceLimits` |
| Database collections | 09 — Database and API Contracts | All collection schemas and indexes |
| Tenant governance | 11 — Security, Privacy, Tenant Governance | Source scope, privacy mode, promotion governance |
| Acceptance tests | 12 — MVP Sequence | Platform capability check as Gate 0 prerequisite |
