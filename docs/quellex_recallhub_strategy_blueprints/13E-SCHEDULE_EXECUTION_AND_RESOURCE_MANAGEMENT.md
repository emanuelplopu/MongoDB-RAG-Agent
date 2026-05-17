# Blueprint 13E — Schedule Execution and Resource Management

> Resolves specification gaps: **B5-1** (Timezone/DST), **B5-2** (Pause/Resume semantics), **B5-3** (StopConditions detail), **ID-4** (Ollama concurrency), **OPS-4** (MongoDB failover), **EC-6** (Resource contention).

> Extends: [05-OVERNIGHT_EXPLORATION_SCHEDULER](./05-OVERNIGHT_EXPLORATION_SCHEDULER.md), [08-RUNTIME_PERFORMANCE_AND_RESOURCE_ROUTING](./08-RUNTIME_PERFORMANCE_AND_RESOURCE_ROUTING.md)

---

## 1. Schedule Timezone and DST Handling

### 1.1 Storage Convention

All schedule times are stored as timezone-aware datetimes using IANA timezone identifiers.

```python
from pydantic import BaseModel
from zoneinfo import ZoneInfo

class ScheduleTimezoneConfig(BaseModel):
    """Timezone configuration for a strategy schedule."""
    timezone: str = "Europe/Vienna"  # IANA timezone name
    allowed_window: str = "22:00-06:00"  # Interpreted in schedule timezone
    skip_nonexistent_hours: bool = True  # Skip runs in DST gap
    dedup_ambiguous_hours: bool = True  # Prevent double-run in DST overlap
```

### 1.2 Cron Evaluation with Timezone

The scheduler uses `croniter` with an explicit timezone parameter to compute next run times.

```python
from croniter import croniter
from datetime import datetime
from zoneinfo import ZoneInfo

def compute_next_run(cron_expr: str, timezone: str, anchor: datetime) -> datetime:
    """Compute next scheduled run in the schedule's local timezone."""
    tz = ZoneInfo(timezone)
    local_now = anchor.astimezone(tz)
    cron = croniter(cron_expr, local_now)
    next_run = cron.get_next(datetime)
    return next_run.replace(tzinfo=tz)
```

### 1.3 DST Spring Forward Handling

When clocks jump forward (e.g., 02:00 → 03:00 in `Europe/Vienna`), a scheduled run at 02:30 falls in a non-existent hour.

**Behavior**: Skip the non-existent time and advance to the next valid evaluation.

```python
from datetime import datetime
from zoneinfo import ZoneInfo

def is_nonexistent_time(dt: datetime, tz: ZoneInfo) -> bool:
    """Check if a local time falls in a DST gap (spring forward)."""
    try:
        dt.replace(tzinfo=tz)
        # Fold check: create naive, localize, compare
        naive = dt.replace(tzinfo=None)
        localized = naive.replace(tzinfo=tz)
        # If UTC offset doesn't round-trip, time is in the gap
        return localized.utcoffset() != dt.utcoffset()
    except Exception:
        return True
```

**Example — Vienna Spring Forward (last Sunday of March):**

```text
Schedule: cron "30 2 * * *" timezone="Europe/Vienna"
Date: 2026-03-29 (spring forward: 02:00 → 03:00)

Expected run: 02:30 CET → does not exist
Action: SKIP, log "Skipped run for schedule {id}: 02:30 falls in DST gap"
Next valid run: 2026-03-30 02:30 CEST
```

### 1.4 DST Fall Back Handling

When clocks fall back (e.g., 03:00 → 02:00 in `Europe/Vienna`), the 02:00–03:00 hour occurs twice.

**Behavior**: Run once only. Deduplication key = `schedule_id + target_date` (date granularity prevents double execution).

```python
def make_dedup_key(schedule_id: str, run_time: datetime) -> str:
    """Generate deduplication key for DST fall-back protection."""
    target_date = run_time.date().isoformat()
    return f"{schedule_id}:{target_date}"
```

**Example — Vienna Fall Back (last Sunday of October):**

```text
Schedule: cron "30 2 * * *" timezone="Europe/Vienna"
Date: 2026-10-25 (fall back: 03:00 → 02:00)

02:30 CEST (first occurrence, UTC+2): RUN ✓
02:30 CET  (second occurrence, UTC+1): SKIP (dedup key exists)
```

### 1.5 Allowed Window Enforcement

The `allowed_window` is always interpreted in the schedule's timezone.

```python
from datetime import time, datetime
from zoneinfo import ZoneInfo

class WindowEnforcer:
    """Determines if current time is within the allowed execution window."""

    def __init__(self, window: str, timezone: str):
        start_str, end_str = window.split("-")
        self.start = time.fromisoformat(start_str)
        self.end = time.fromisoformat(end_str)
        self.tz = ZoneInfo(timezone)
        self.crosses_midnight = self.start > self.end

    def is_within_window(self, utc_now: datetime) -> bool:
        local_now = utc_now.astimezone(self.tz)
        current_time = local_now.time()

        if self.crosses_midnight:
            return current_time >= self.start or current_time <= self.end
        else:
            return self.start <= current_time <= self.end
```

If DST causes the window to shrink (e.g., an 8-hour window becomes 7 hours on spring-forward night), remaining runs within the valid portion of the window still execute. Runs that would fall outside the window after the shift are deferred to the next night.

### 1.6 ScheduleTimezoneResolver

```python
from dataclasses import dataclass
from datetime import datetime, date
from zoneinfo import ZoneInfo
from croniter import croniter
from typing import Optional
import logging

logger = logging.getLogger(__name__)

@dataclass
class ScheduleRunDecision:
    should_run: bool
    next_run: Optional[datetime]
    skip_reason: Optional[str] = None

class ScheduleTimezoneResolver:
    """Resolves timezone and DST edge cases for schedule execution."""

    def __init__(self, timezone: str, cron_expr: str, allowed_window: str):
        self.tz = ZoneInfo(timezone)
        self.cron_expr = cron_expr
        self.window = WindowEnforcer(allowed_window, timezone)
        self._executed_dates: set[str] = set()

    def evaluate_next_run(self, utc_now: datetime) -> ScheduleRunDecision:
        """Determine if the next scheduled run should execute."""
        local_now = utc_now.astimezone(self.tz)
        cron = croniter(self.cron_expr, local_now)
        candidate = cron.get_next(datetime)

        # DST gap check (spring forward)
        if self._is_in_dst_gap(candidate):
            logger.warning(f"Run at {candidate} falls in DST gap, skipping")
            return ScheduleRunDecision(
                should_run=False,
                next_run=cron.get_next(datetime),
                skip_reason="dst_gap"
            )

        # DST overlap dedup (fall back)
        dedup_key = candidate.date().isoformat()
        if dedup_key in self._executed_dates:
            logger.info(f"Run already executed for {dedup_key}, skipping duplicate")
            return ScheduleRunDecision(
                should_run=False,
                next_run=cron.get_next(datetime),
                skip_reason="dst_overlap_dedup"
            )

        # Window check
        if not self.window.is_within_window(candidate):
            return ScheduleRunDecision(
                should_run=False,
                next_run=candidate,
                skip_reason="outside_window"
            )

        return ScheduleRunDecision(should_run=True, next_run=candidate)

    def mark_executed(self, run_time: datetime) -> None:
        self._executed_dates.add(run_time.date().isoformat())

    def _is_in_dst_gap(self, dt: datetime) -> bool:
        """Detect if datetime falls in a DST spring-forward gap."""
        try:
            naive = dt.replace(tzinfo=None)
            # Attempt round-trip through timezone
            localized = naive.replace(tzinfo=self.tz)
            roundtrip = localized.astimezone(self.tz)
            return roundtrip.replace(tzinfo=None) != naive
        except Exception:
            return True
```

---

## 2. Pause/Resume Semantics

### 2.1 Pause Triggers

| Trigger Config Key | Detection Method | Threshold |
|---|---|---|
| `pause_if_interactive_users` | Active SSE connections OR chat API calls within 5 min | ≥ 1 connection or call |
| `pause_if_backend_chat_active` | `activity_logs` collection entries within 5 min | ≥ 1 entry |
| `pause_if_resource_pressure` | System metrics polling | CPU > 80% OR RAM > 85% OR GPU > 90% |

### 2.2 Pause Behavior State Machine

```text
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ┌─────────┐    pause triggered    ┌────────┐                   │
│  │ RUNNING │ ───────────────────> │ PAUSED │                   │
│  └─────────┘                       └────────┘                   │
│       ^                                │                         │
│       │    all conditions clear        │  max_pause exceeded     │
│       │    (within max_pause)          │                         │
│       │                                v                         │
│       │                          ┌──────────┐                   │
│       └──────────────────────────│ DEFERRED │                   │
│              (never auto-resume)  └──────────┘                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Check interval**: Every 60 seconds, re-evaluate all pause conditions.

**Resume**: When ALL active pause conditions simultaneously clear, scheduler transitions from PAUSED → RUNNING.

### 2.3 Max Pause Duration

```python
class PauseConfig(BaseModel):
    """Configuration for pause behavior."""
    max_pause_minutes: int = 120  # Maximum time to stay paused
    check_interval_seconds: int = 60  # How often to re-evaluate
    pause_if_interactive_users: bool = True
    pause_if_backend_chat_active: bool = True
    pause_if_resource_pressure: bool = True
    resource_thresholds: "ResourceThresholds" = None

class ResourceThresholds(BaseModel):
    cpu_pct: float = 80.0
    ram_pct: float = 85.0
    gpu_pct: float = 90.0
```

If `max_pause_minutes` is exceeded:
1. Job transitions to `deferred` state
2. Deferred jobs appear in the nightly report under "Skipped due to user activity"
3. Deferred jobs are **NOT** automatically retried; the next scheduled run creates a fresh job

### 2.4 Partial Execution Handling

When an experiment is mid-run at pause time (e.g., 5 of 10 strategies completed):

**On Resume** (within max_pause):
- Continue from the next unfinished strategy (checkpoint stored in experiment job document)
- Checkpoint format: `{"last_completed_index": 5, "completed_strategy_ids": [...]}`

**On Defer** (max_pause exceeded):
- Preserve all completed results
- Mark remaining strategies as `"not_executed"` with reason `"deferred_due_to_pause_timeout"`
- Final experiment status: `"partial"`

### 2.5 PauseConditionEvaluator

```python
import asyncio
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

class PauseState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    DEFERRED = "deferred"

class PauseReason(BaseModel):
    trigger: str
    detected_at: datetime
    details: Optional[str] = None

class PauseConditionEvaluator:
    """Evaluates pause conditions and manages scheduler pause state."""

    def __init__(
        self,
        config: PauseConfig,
        db,  # AsyncMongoClient database reference
        resource_monitor,  # ResourceMonitor instance
    ):
        self.config = config
        self.db = db
        self.resource_monitor = resource_monitor
        self._state = PauseState.RUNNING
        self._paused_at: Optional[datetime] = None
        self._active_reasons: list[PauseReason] = []

    @property
    def state(self) -> PauseState:
        return self._state

    async def evaluate(self) -> PauseState:
        """Evaluate all pause conditions. Returns new state."""
        if self._state == PauseState.DEFERRED:
            return PauseState.DEFERRED  # Terminal for this job

        reasons: list[PauseReason] = []
        now = datetime.utcnow()

        # Check interactive users (SSE connections + recent chat)
        if self.config.pause_if_interactive_users:
            if await self._has_active_sse_connections():
                reasons.append(PauseReason(
                    trigger="interactive_users",
                    detected_at=now,
                    details="Active SSE connections detected"
                ))
            elif await self._has_recent_chat_calls(minutes=5):
                reasons.append(PauseReason(
                    trigger="interactive_users",
                    detected_at=now,
                    details="Recent chat API calls within 5 minutes"
                ))

        # Check backend chat activity
        if self.config.pause_if_backend_chat_active:
            if await self._has_backend_chat_activity(minutes=5):
                reasons.append(PauseReason(
                    trigger="backend_chat_active",
                    detected_at=now,
                    details="Activity logs show chat within 5 minutes"
                ))

        # Check resource pressure
        if self.config.pause_if_resource_pressure:
            metrics = await self.resource_monitor.get_current_metrics()
            thresholds = self.config.resource_thresholds
            if (metrics.cpu_pct > thresholds.cpu_pct or
                metrics.ram_pct > thresholds.ram_pct or
                metrics.gpu_pct > thresholds.gpu_pct):
                reasons.append(PauseReason(
                    trigger="resource_pressure",
                    detected_at=now,
                    details=f"CPU={metrics.cpu_pct:.0f}% RAM={metrics.ram_pct:.0f}% GPU={metrics.gpu_pct:.0f}%"
                ))

        # State transitions
        if reasons:
            if self._state == PauseState.RUNNING:
                self._state = PauseState.PAUSED
                self._paused_at = now
                self._active_reasons = reasons
                logger.info(f"Scheduler PAUSED: {[r.trigger for r in reasons]}")
            elif self._state == PauseState.PAUSED:
                # Check max pause duration
                elapsed = (now - self._paused_at).total_seconds() / 60
                if elapsed >= self.config.max_pause_minutes:
                    self._state = PauseState.DEFERRED
                    logger.warning(
                        f"Scheduler DEFERRED: paused for {elapsed:.0f} min "
                        f"(max={self.config.max_pause_minutes})"
                    )
        else:
            # All conditions clear
            if self._state == PauseState.PAUSED:
                self._state = PauseState.RUNNING
                elapsed = (now - self._paused_at).total_seconds() / 60
                logger.info(f"Scheduler RESUMED after {elapsed:.1f} min pause")
                self._paused_at = None
                self._active_reasons = []

        return self._state

    async def _has_active_sse_connections(self) -> bool:
        """Check if any SSE streaming connections are active."""
        # Implementation: query connection registry or counter
        count = await self.db["sse_connections"].count_documents({"active": True})
        return count > 0

    async def _has_recent_chat_calls(self, minutes: int) -> bool:
        """Check for recent chat API calls."""
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        count = await self.db["activity_logs"].count_documents({
            "type": "chat_request",
            "timestamp": {"$gte": cutoff}
        })
        return count > 0

    async def _has_backend_chat_activity(self, minutes: int) -> bool:
        """Check activity_logs for backend chat activity."""
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        count = await self.db["activity_logs"].count_documents({
            "timestamp": {"$gte": cutoff}
        })
        return count > 0
```

---

## 3. StopConditions Model

### 3.1 Model Definition

```python
from pydantic import BaseModel, Field
from typing import Optional

class StopConditions(BaseModel):
    """Conditions that cause an exploration experiment to stop early."""
    max_runtime_minutes: int = Field(
        default=360,
        description="Maximum wall-clock runtime for the entire experiment (6 hours)"
    )
    max_llm_calls: int = Field(
        default=500,
        description="Total LLM API calls across all strategy runs in this experiment"
    )
    max_cost_usd: float = Field(
        default=50.0,
        description="Aggregate cost cap estimated from token counts and model pricing"
    )
    stop_on_leader_found: bool = Field(
        default=True,
        description="Stop early if one candidate decisively beats all others"
    )
    leader_margin_pct: float = Field(
        default=10.0,
        description="Percentage by which leader must beat second-best composite score"
    )
    stop_on_fatal_failure_rate: float = Field(
        default=0.5,
        description="Stop if more than this fraction of candidates fail fatally"
    )
    min_candidates_completed: int = Field(
        default=3,
        description="Minimum candidates that must complete before early stop is allowed"
    )
    max_consecutive_failures: int = Field(
        default=5,
        description="Stop if this many candidates in a row fail"
    )
```

### 3.2 Evaluation Priority Order

Stop conditions are evaluated after each candidate completes. Priority (highest first):

1. **Safety stops** (always checked first):
   - `max_consecutive_failures` — immediate stop, experiment marked `failed`
   - `stop_on_fatal_failure_rate` — immediate stop, experiment marked `failed`
   - `max_runtime_minutes` — immediate stop, experiment marked `timeout`

2. **Budget stops**:
   - `max_cost_usd` — stop, experiment marked `budget_exhausted`
   - `max_llm_calls` — stop, experiment marked `budget_exhausted`

3. **Optimization stops** (only after `min_candidates_completed` met):
   - `stop_on_leader_found` with `leader_margin_pct` — graceful stop, experiment marked `leader_found`

### 3.3 Cost Estimation

```python
from typing import Optional

# Pricing table (USD per million tokens) — updated from LiteLLM registry
MODEL_PRICING = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "gemma3:27b": {"input": 0.0, "output": 0.0},  # Local model, no API cost
    "qwen3:30b-a3b": {"input": 0.0, "output": 0.0},  # Local model
}

def estimate_call_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    reported_cost: Optional[float] = None
) -> float:
    """Estimate cost for a single LLM call.

    Uses provider-reported cost if available; otherwise estimates
    from token count and model pricing table.
    """
    if reported_cost is not None:
        return reported_cost

    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return 0.0  # Unknown model, assume local/free

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost
```

### 3.4 Leader Detection Algorithm

```python
def detect_leader(
    results: list[dict],  # [{"strategy_id": str, "composite_score": float, "status": str}]
    margin_pct: float,
    min_completed: int
) -> Optional[str]:
    """Detect if a clear leader has emerged.

    Returns leader strategy_id if conditions met, else None.
    """
    completed = [r for r in results if r["status"] == "completed"]

    if len(completed) < min_completed:
        return None

    # Sort by composite score descending
    sorted_results = sorted(completed, key=lambda r: r["composite_score"], reverse=True)

    if len(sorted_results) < 2:
        return None

    best = sorted_results[0]
    second_best = sorted_results[1]

    if second_best["composite_score"] == 0:
        return best["strategy_id"]

    margin = ((best["composite_score"] - second_best["composite_score"])
              / second_best["composite_score"]) * 100

    if margin >= margin_pct:
        return best["strategy_id"]

    return None
```

### 3.5 StopConditionEvaluator

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum

class StopReason(str, Enum):
    NONE = "none"
    MAX_RUNTIME = "max_runtime_exceeded"
    MAX_LLM_CALLS = "max_llm_calls_exceeded"
    MAX_COST = "max_cost_exceeded"
    LEADER_FOUND = "leader_found"
    FATAL_FAILURE_RATE = "fatal_failure_rate_exceeded"
    CONSECUTIVE_FAILURES = "max_consecutive_failures"

@dataclass
class StopDecision:
    should_stop: bool
    reason: StopReason
    details: Optional[str] = None

class StopConditionEvaluator:
    """Evaluates experiment stop conditions after each candidate run."""

    def __init__(self, conditions: StopConditions, experiment_start: datetime):
        self.conditions = conditions
        self.experiment_start = experiment_start
        self._total_llm_calls: int = 0
        self._total_cost_usd: float = 0.0
        self._consecutive_failures: int = 0
        self._results: list[dict] = []

    def record_candidate_result(
        self,
        strategy_id: str,
        composite_score: float,
        status: str,  # "completed" | "failed" | "fatal"
        llm_calls: int,
        cost_usd: float,
    ) -> None:
        """Record a completed candidate for evaluation."""
        self._total_llm_calls += llm_calls
        self._total_cost_usd += cost_usd
        self._results.append({
            "strategy_id": strategy_id,
            "composite_score": composite_score,
            "status": status,
        })

        if status in ("failed", "fatal"):
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0

    def evaluate(self) -> StopDecision:
        """Evaluate all stop conditions in priority order."""
        # --- Safety stops (highest priority) ---

        # Consecutive failures
        if self._consecutive_failures >= self.conditions.max_consecutive_failures:
            return StopDecision(
                should_stop=True,
                reason=StopReason.CONSECUTIVE_FAILURES,
                details=f"{self._consecutive_failures} consecutive failures"
            )

        # Fatal failure rate
        if len(self._results) > 0:
            fatal_count = sum(1 for r in self._results if r["status"] == "fatal")
            fatal_rate = fatal_count / len(self._results)
            if fatal_rate > self.conditions.stop_on_fatal_failure_rate:
                return StopDecision(
                    should_stop=True,
                    reason=StopReason.FATAL_FAILURE_RATE,
                    details=f"Fatal rate {fatal_rate:.0%} > {self.conditions.stop_on_fatal_failure_rate:.0%}"
                )

        # Runtime
        elapsed_minutes = (datetime.utcnow() - self.experiment_start).total_seconds() / 60
        if elapsed_minutes >= self.conditions.max_runtime_minutes:
            return StopDecision(
                should_stop=True,
                reason=StopReason.MAX_RUNTIME,
                details=f"Elapsed {elapsed_minutes:.0f} min >= {self.conditions.max_runtime_minutes} min"
            )

        # --- Budget stops ---

        if self._total_cost_usd >= self.conditions.max_cost_usd:
            return StopDecision(
                should_stop=True,
                reason=StopReason.MAX_COST,
                details=f"Cost ${self._total_cost_usd:.2f} >= ${self.conditions.max_cost_usd:.2f}"
            )

        if self._total_llm_calls >= self.conditions.max_llm_calls:
            return StopDecision(
                should_stop=True,
                reason=StopReason.MAX_LLM_CALLS,
                details=f"LLM calls {self._total_llm_calls} >= {self.conditions.max_llm_calls}"
            )

        # --- Optimization stops ---

        if self.conditions.stop_on_leader_found:
            leader = detect_leader(
                self._results,
                self.conditions.leader_margin_pct,
                self.conditions.min_candidates_completed
            )
            if leader:
                return StopDecision(
                    should_stop=True,
                    reason=StopReason.LEADER_FOUND,
                    details=f"Leader: {leader} (margin >= {self.conditions.leader_margin_pct}%)"
                )

        return StopDecision(should_stop=False, reason=StopReason.NONE)
```

---

## 4. Ollama Concurrency and Residency Management

### 4.1 OllamaResidencyManager

```python
import asyncio
import time
import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field
from pydantic import BaseModel
import httpx

logger = logging.getLogger(__name__)

@dataclass
class ModelResidencyInfo:
    """Tracks a model's residency state in Ollama."""
    model_name: str
    is_loaded: bool = False
    last_seen_loaded: Optional[datetime] = None
    last_used: Optional[datetime] = None
    load_duration_ms_history: list[int] = field(default_factory=list)
    inference_count: int = 0
    vram_size_bytes: Optional[int] = None

    @property
    def estimated_load_time_ms(self) -> int:
        """Estimate load time from historical data."""
        if not self.load_duration_ms_history:
            return 15000  # Default 15s estimate for unknown models
        return int(sum(self.load_duration_ms_history) / len(self.load_duration_ms_history))


class OllamaResidencyManager:
    """Manages Ollama model residency, concurrency, and priority scheduling.

    Responsibilities:
    - Track which models are currently loaded via polling
    - Coordinate concurrent access with per-model mutexes
    - Implement interactive-priority preemption for scheduler
    - Prewarm models before overnight experiment runs
    - Detect and mitigate model thrashing
    """

    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        poll_interval_seconds: float = 30.0,
        max_wait_ms: int = 30000,
        keep_alive_override: str = "30m",
        thrashing_threshold: int = 5,
        thrashing_window_minutes: int = 10,
    ):
        self._base_url = ollama_base_url
        self._poll_interval = poll_interval_seconds
        self._max_wait_ms = max_wait_ms
        self._keep_alive = keep_alive_override
        self._thrashing_threshold = thrashing_threshold
        self._thrashing_window_minutes = thrashing_window_minutes

        # Per-model concurrency locks
        self._model_locks: dict[str, asyncio.Lock] = {}
        # Model residency tracking
        self._residency: dict[str, ModelResidencyInfo] = {}
        # Interactive priority flag
        self._interactive_priority_flag = asyncio.Event()
        # Load event timestamps for thrashing detection
        self._load_events: list[datetime] = []
        # Background poll task
        self._poll_task: Optional[asyncio.Task] = None
        # HTTP client
        self._client = httpx.AsyncClient(base_url=ollama_base_url, timeout=30.0)

    async def start(self) -> None:
        """Start the background residency polling loop."""
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("OllamaResidencyManager started")

    async def stop(self) -> None:
        """Stop polling and cleanup."""
        if self._poll_task:
            self._poll_task.cancel()
        await self._client.aclose()

    async def _poll_loop(self) -> None:
        """Continuously poll Ollama for loaded models."""
        while True:
            try:
                await self._refresh_residency()
            except Exception as e:
                logger.warning(f"Residency poll failed: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _refresh_residency(self) -> None:
        """Query GET /api/ps to update residency state."""
        resp = await self._client.get("/api/ps")
        resp.raise_for_status()
        data = resp.json()

        currently_loaded = set()
        for model_info in data.get("models", []):
            name = model_info["name"]
            currently_loaded.add(name)

            if name not in self._residency:
                self._residency[name] = ModelResidencyInfo(model_name=name)

            info = self._residency[name]
            info.is_loaded = True
            info.last_seen_loaded = datetime.utcnow()
            if "size_vram" in model_info:
                info.vram_size_bytes = model_info["size_vram"]

        # Mark models not in response as unloaded
        for name, info in self._residency.items():
            if name not in currently_loaded:
                info.is_loaded = False

    async def ensure_model_loaded(self, model_name: str) -> int:
        """Ensure a model is loaded in Ollama. Returns load time in ms.

        If already loaded, returns 0. If not loaded, triggers load and waits.
        """
        info = self._residency.get(model_name)
        if info and info.is_loaded:
            return 0

        # Trigger model load
        start = time.monotonic()
        logger.info(f"Loading model {model_name}...")

        resp = await self._client.post("/api/generate", json={
            "model": model_name,
            "prompt": "",
            "keep_alive": self._keep_alive,
        })
        resp.raise_for_status()

        load_ms = int((time.monotonic() - start) * 1000)

        # Update residency
        if model_name not in self._residency:
            self._residency[model_name] = ModelResidencyInfo(model_name=model_name)
        info = self._residency[model_name]
        info.is_loaded = True
        info.last_seen_loaded = datetime.utcnow()
        info.load_duration_ms_history.append(load_ms)
        # Keep only last 10 measurements
        info.load_duration_ms_history = info.load_duration_ms_history[-10:]

        # Record load event for thrashing detection
        self._load_events.append(datetime.utcnow())
        self._check_thrashing()

        logger.info(f"Model {model_name} loaded in {load_ms}ms")
        return load_ms

    def estimate_load_time(self, model_name: str) -> int:
        """Estimate how long it will take to load a model (ms)."""
        info = self._residency.get(model_name)
        if info:
            return info.estimated_load_time_ms
        return 15000  # Default for unknown models

    async def acquire_model(self, model_name: str) -> bool:
        """Acquire exclusive access to a model. Returns False if timeout exceeded."""
        if model_name not in self._model_locks:
            self._model_locks[model_name] = asyncio.Lock()

        lock = self._model_locks[model_name]
        try:
            await asyncio.wait_for(
                lock.acquire(),
                timeout=self._max_wait_ms / 1000
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Timeout acquiring model {model_name} after {self._max_wait_ms}ms")
            return False

    def release_model(self, model_name: str) -> None:
        """Release exclusive model access."""
        lock = self._model_locks.get(model_name)
        if lock and lock.locked():
            lock.release()

    def set_interactive_priority(self) -> None:
        """Signal that an interactive request needs model priority."""
        self._interactive_priority_flag.set()

    def clear_interactive_priority(self) -> None:
        """Clear the interactive priority signal."""
        self._interactive_priority_flag.clear()

    def should_yield_to_interactive(self) -> bool:
        """Check if scheduler should yield model to interactive user."""
        return self._interactive_priority_flag.is_set()

    async def prewarm_models(self, model_names: list[str]) -> dict[str, int]:
        """Prewarm multiple models for overnight runs.

        Returns dict of model_name -> load_time_ms.
        """
        results = {}
        for model_name in model_names:
            load_ms = await self.ensure_model_loaded(model_name)
            results[model_name] = load_ms
        return results

    def _check_thrashing(self) -> None:
        """Detect model loading thrashing."""
        cutoff = datetime.utcnow() - __import__("datetime").timedelta(
            minutes=self._thrashing_window_minutes
        )
        recent_loads = [t for t in self._load_events if t >= cutoff]
        self._load_events = recent_loads  # Prune old events

        if len(recent_loads) > self._thrashing_threshold:
            logger.warning(
                f"Model thrashing detected: {len(recent_loads)} loads in "
                f"last {self._thrashing_window_minutes} minutes"
            )

    def get_residency_status(self) -> dict[str, dict]:
        """Get current residency status for all tracked models."""
        return {
            name: {
                "is_loaded": info.is_loaded,
                "last_used": info.last_used.isoformat() if info.last_used else None,
                "estimated_load_ms": info.estimated_load_time_ms,
                "inference_count": info.inference_count,
            }
            for name, info in self._residency.items()
        }
```

### 4.2 Concurrency Coordination

```text
┌─────────────────────────────────────────────────────┐
│             Model Access Flow                        │
├─────────────────────────────────────────────────────┤
│                                                     │
│   Interactive Request                               │
│   ┌──────────┐                                     │
│   │ Priority │──> set_interactive_priority()        │
│   │  Queue   │──> acquire_model(model)             │
│   └──────────┘──> [inference] ──> release_model()  │
│                ──> clear_interactive_priority()     │
│                                                     │
│   Scheduler Request                                 │
│   ┌──────────┐                                     │
│   │ Standard │──> check should_yield_to_interactive │
│   │  Queue   │    ├─ YES: pause node, wait         │
│   └──────────┘    └─ NO: acquire_model(model)      │
│                        ──> [inference]              │
│                        ──> release_model()          │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Priority Rules:**
- Interactive requests: immediate priority, bypass scheduler queue
- Scheduler requests: check `interactive_priority_flag` before each node dispatch
- If flag is set: scheduler yields model, current node finishes, next node pauses
- If model busy and max_wait exceeded: fallback to alternative model (if defined) or fail with `model_busy`

### 4.3 Model Prewarming for Overnight Runs

Before an experiment starts, prewarm models needed by the first N candidates (N = `max_parallel_runs`):

```python
async def prewarm_for_experiment(
    residency_manager: OllamaResidencyManager,
    candidates: list[dict],
    max_parallel: int
) -> None:
    """Prewarm models needed by the first batch of candidates."""
    models_needed = set()
    for candidate in candidates[:max_parallel]:
        for role_config in candidate.get("model_roles", {}).values():
            if role_config.get("provider") == "ollama":
                models_needed.add(role_config["model"])

    logger.info(f"Prewarming {len(models_needed)} models: {models_needed}")
    await residency_manager.prewarm_models(list(models_needed))
```

**Prewarming method**: `POST /api/generate` with empty prompt triggers model load without inference overhead.

**Keep-alive during overnight**: Set `OLLAMA_KEEP_ALIVE=30m` to prevent idle eviction during the experiment window.

### 4.4 Thrashing Mitigation

When thrashing is detected (> 5 model loads in 10 minutes):

1. Log `WARNING "Model thrashing detected"`
2. Mitigation: sort remaining candidates by model requirement and run all candidates using the same model consecutively before switching

```python
def sort_candidates_by_model(candidates: list[dict]) -> list[dict]:
    """Sort candidates to minimize model switching.

    Groups candidates by their primary model, reducing load/evict cycles.
    """
    from itertools import groupby

    def primary_model(candidate: dict) -> str:
        roles = candidate.get("model_roles", {})
        synth = roles.get("synthesizer_deep") or roles.get("synthesizer_fast")
        return synth.get("model", "") if synth else ""

    return sorted(candidates, key=primary_model)
```

---

## 5. MongoDB Failover Resilience

### 5.1 Strategy Spec Cache

```python
import asyncio
import time
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class StrategySpecCache:
    """In-memory cache for strategy specifications with TTL and DB fallback.

    Provides read resilience when MongoDB is temporarily unavailable.
    """

    def __init__(self, ttl_seconds: float = 300.0):  # 5 minute TTL
        self._cache: dict[str, dict] = {}
        self._last_refresh: float = 0
        self._ttl = ttl_seconds
        self._db = None
        self._lock = asyncio.Lock()

    def set_db(self, db) -> None:
        self._db = db

    async def get(self, strategy_id: str) -> Optional[dict]:
        """Get strategy spec, refreshing from DB if TTL expired."""
        if self._is_expired():
            await self._try_refresh()

        return self._cache.get(strategy_id)

    async def get_all(self) -> dict[str, dict]:
        """Get all cached strategy specs."""
        if self._is_expired():
            await self._try_refresh()
        return dict(self._cache)

    def _is_expired(self) -> bool:
        return (time.monotonic() - self._last_refresh) > self._ttl

    async def _try_refresh(self) -> None:
        """Attempt to refresh cache from DB. Serve stale on failure."""
        async with self._lock:
            if not self._is_expired():
                return  # Another coroutine already refreshed

            try:
                cursor = self._db["strategy_specs"].find({"active": True})
                specs = {}
                async for doc in cursor:
                    specs[doc["strategy_id"]] = doc
                self._cache = specs
                self._last_refresh = time.monotonic()
                logger.debug(f"Strategy spec cache refreshed: {len(specs)} specs")
            except Exception as e:
                if self._cache:
                    logger.warning(f"DB unavailable, serving cached strategy specs: {e}")
                else:
                    logger.error(f"DB unavailable and cache empty: {e}")
                    raise

    def invalidate(self) -> None:
        """Force cache refresh on next access."""
        self._last_refresh = 0
```

### 5.2 Result Buffer

```python
from collections import deque
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

class ResultBuffer:
    """Buffers DB writes when MongoDB is unavailable.

    Items are flushed to the database when connectivity is restored.
    On clean shutdown, performs a final flush attempt.
    """

    def __init__(self, db, max_size: int = 100, flush_interval_seconds: float = 30.0):
        self._db = db
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_size)
        self._max_size = max_size
        self._flush_interval = flush_interval_seconds
        self._flush_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start background flush loop."""
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop flush loop and attempt final drain."""
        if self._flush_task:
            self._flush_task.cancel()
        await self._final_flush(timeout=5.0)

    async def write(self, collection: str, document: dict) -> bool:
        """Attempt to write to DB; buffer on failure.

        Returns True if written to DB, False if buffered.
        """
        try:
            await self._db[collection].insert_one(document)
            return True
        except Exception as e:
            logger.warning(f"DB write failed, buffering: {e}")
            self._buffer_item(collection, document)
            return False

    def _buffer_item(self, collection: str, document: dict) -> None:
        """Add item to buffer. Drops oldest if full."""
        if len(self._buffer) >= self._max_size:
            dropped = self._buffer.popleft()
            logger.warning(f"Buffer full, dropping oldest item: {dropped.get('collection')}")

        self._buffer.append({"collection": collection, "document": document})

    async def _flush_loop(self) -> None:
        """Periodically attempt to flush buffered items."""
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """Attempt to write all buffered items to DB."""
        if not self._buffer:
            return

        flushed = 0
        while self._buffer:
            item = self._buffer[0]
            try:
                await self._db[item["collection"]].insert_one(item["document"])
                self._buffer.popleft()
                flushed += 1
            except Exception:
                break  # DB still down, stop trying

        if flushed > 0:
            logger.info(f"Flushed {flushed} buffered items to DB")

    async def _final_flush(self, timeout: float) -> None:
        """Attempt one final flush on shutdown."""
        if not self._buffer:
            return
        try:
            await asyncio.wait_for(self._flush(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Final flush timed out, {len(self._buffer)} items lost")

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)
```

### 5.3 Circuit Breaker Pattern

```python
import asyncio
import time
import logging
from enum import Enum
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, all writes buffered
    HALF_OPEN = "half_open"  # Testing recovery

class MongoDBCircuitBreaker:
    """Circuit breaker for MongoDB operations.

    State transitions:
        CLOSED → OPEN:      after failure_threshold consecutive failures within window
        OPEN → HALF_OPEN:   after recovery_timeout elapses
        HALF_OPEN → CLOSED: if test operation succeeds
        HALF_OPEN → OPEN:   if test operation fails (resets timer)
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        failure_window_seconds: float = 60.0,
        recovery_timeout_seconds: float = 30.0,
    ):
        self._failure_threshold = failure_threshold
        self._failure_window = failure_window_seconds
        self._recovery_timeout = recovery_timeout_seconds

        self._state = CircuitState.CLOSED
        self._failure_timestamps: list[float] = []
        self._last_state_change: float = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        # Auto-transition OPEN → HALF_OPEN after timeout
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker: OPEN → HALF_OPEN (testing recovery)")
        return self._state

    async def execute(
        self,
        operation: Callable,
        *args,
        fallback: Optional[Callable] = None,
        **kwargs
    ) -> Any:
        """Execute a DB operation through the circuit breaker.

        Args:
            operation: Async callable to execute
            fallback: Optional fallback if circuit is open
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            if fallback:
                return await fallback(*args, **kwargs)
            raise CircuitOpenError("Circuit breaker is OPEN, DB operations unavailable")

        try:
            result = await operation(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            if fallback and self.state == CircuitState.OPEN:
                return await fallback(*args, **kwargs)
            raise

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_timestamps.clear()
                self._last_state_change = time.monotonic()
                logger.info("Circuit breaker: HALF_OPEN → CLOSED (recovery confirmed)")

    async def _on_failure(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._failure_timestamps.append(now)

            # Prune old failures outside window
            cutoff = now - self._failure_window
            self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_state_change = now
                logger.warning("Circuit breaker: HALF_OPEN → OPEN (test failed)")
            elif (self._state == CircuitState.CLOSED and
                  len(self._failure_timestamps) >= self._failure_threshold):
                self._state = CircuitState.OPEN
                self._last_state_change = now
                logger.warning(
                    f"Circuit breaker: CLOSED → OPEN "
                    f"({len(self._failure_timestamps)} failures in {self._failure_window}s)"
                )

    def is_available(self) -> bool:
        """Quick check if DB operations are likely to succeed."""
        return self.state != CircuitState.OPEN


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and no fallback is available."""
    pass
```

### 5.4 Scheduler Behavior During DB Outage

| Scenario | Behavior |
|---|---|
| Schedule due but DB unavailable | Log WARNING, skip this poll cycle, try again at next interval |
| Experiment mid-run, DB write fails | Buffer result; continue experiment with local state |
| DB recovers during experiment | Flush buffer; resume normal writes |
| DB unavailable for entire night | Log in nightly report: "Missed schedules due to DB unavailability" |

**Critical rule**: Do NOT queue missed schedules for bulk execution on recovery. This prevents a resource flood that could overwhelm the system.

---

## 6. Resource Contention Between Scheduler and Interactive Users

### 6.1 Priority Model

**Absolute rule: Interactive users ALWAYS have priority over the scheduler.**

### 6.2 Detection of Interactive Activity

| Method | Signal | Detection |
|---|---|---|
| SSE Connections | Real-time streaming active | Connection count > 0 in registry |
| Recent Chat API | User recently asked a question | `activity_logs` entries within 5 min |
| Active Ingestion | Document processing running | `ingestion_jobs` with status `"running"` |

### 6.3 Contention Levels and Resolution

```text
┌─────────────────────────────────────────────────────────────────────┐
│                  Contention Decision Matrix                          │
├──────────┬───────────────────────────────────────┬──────────────────┤
│  Level   │  Condition                            │  Action          │
├──────────┼───────────────────────────────────────┼──────────────────┤
│  Level 1 │  Scheduler uses different model       │  Both proceed    │
│  (Low)   │  than interactive                     │                  │
├──────────┼───────────────────────────────────────┼──────────────────┤
│  Level 2 │  Same model needed by both            │  Scheduler       │
│  (Model) │  scheduler and interactive            │  pauses current  │
│          │                                       │  node, waits     │
├──────────┼───────────────────────────────────────┼──────────────────┤
│  Level 3 │  CPU > 80% OR RAM > 85% OR           │  Scheduler       │
│(Resource)│  GPU > 90% (regardless of model)      │  pauses entirely │
└──────────┴───────────────────────────────────────┴──────────────────┘
```

### 6.4 Resource Budgets

```python
class SchedulerResourceBudget(BaseModel):
    """Resource budget constraints for the scheduler.

    These limits ensure interactive users always have headroom.
    """
    max_cpu_pct: float = 60.0   # Scheduler may use up to 60% CPU
    max_ram_pct: float = 50.0   # Scheduler may use up to 50% RAM
    max_gpu_pct: float = 70.0   # Scheduler may use up to 70% GPU
    throttle_delay_ms: int = 2000  # Delay between nodes when over budget

class InteractiveResourceBudget(BaseModel):
    """Interactive users have no artificial limits."""
    max_cpu_pct: float = 100.0
    max_ram_pct: float = 100.0
    max_gpu_pct: float = 100.0
```

If the scheduler detects it is consuming resources above its budget, it inserts artificial delays (`throttle_delay_ms`) between orchestration nodes to reduce pressure.

### 6.5 Business Hours Mode

```python
class BusinessHoursConfig(BaseModel):
    """Business hours configuration for graceful degradation."""
    business_hours: str = "08:00-18:00"  # Interpreted in schedule timezone
    business_days: list[int] = [0, 1, 2, 3, 4]  # Mon-Fri (0=Monday)
    during_business_hours_mode: str = "smoke"  # Only smoke tests
    outside_business_hours_mode: str = "exploration"  # Full exploration
```

| Time Period | Scheduler Mode | Behavior |
|---|---|---|
| Business hours (08:00-18:00, Mon-Fri) | `smoke` | Quick validation only, minimal resource use |
| Outside business hours | `exploration` | Full exploration, all candidates run |
| Weekends | `exploration` | Full exploration (configurable) |

### 6.6 ResourceContentionManager

```python
import asyncio
import logging
from datetime import datetime, time as dt_time
from typing import Optional
from enum import Enum
from pydantic import BaseModel
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class ContentionLevel(str, Enum):
    NONE = "none"
    LOW = "low"           # Different models, proceed
    MODEL = "model"       # Same model contention
    RESOURCE = "resource"  # System resource pressure

class ContentionDecision(BaseModel):
    level: ContentionLevel
    action: str  # "proceed" | "pause_node" | "pause_scheduler" | "throttle"
    details: Optional[str] = None
    throttle_ms: int = 0

class ResourceContentionManager:
    """Manages resource contention between scheduler and interactive users.

    Ensures interactive users always have priority while maximizing
    scheduler throughput during idle periods.
    """

    def __init__(
        self,
        db,
        resource_monitor,
        residency_manager: "OllamaResidencyManager",
        scheduler_budget: "SchedulerResourceBudget",
        business_hours_config: "BusinessHoursConfig",
        timezone: str = "Europe/Vienna",
    ):
        self._db = db
        self._resource_monitor = resource_monitor
        self._residency_manager = residency_manager
        self._budget = scheduler_budget
        self._bh_config = business_hours_config
        self._tz = ZoneInfo(timezone)

    async def evaluate_contention(
        self, scheduler_model: Optional[str] = None
    ) -> ContentionDecision:
        """Evaluate current contention level and return action decision.

        Args:
            scheduler_model: Model the scheduler intends to use next
        """
        # Check resource pressure first (highest severity)
        metrics = await self._resource_monitor.get_current_metrics()
        if (metrics.cpu_pct > 80 or metrics.ram_pct > 85 or metrics.gpu_pct > 90):
            return ContentionDecision(
                level=ContentionLevel.RESOURCE,
                action="pause_scheduler",
                details=f"CPU={metrics.cpu_pct:.0f}% RAM={metrics.ram_pct:.0f}% GPU={metrics.gpu_pct:.0f}%"
            )

        # Check for interactive activity
        has_interactive = await self._detect_interactive_activity()

        if not has_interactive:
            # Check if scheduler is over budget (self-throttle)
            if self._is_over_budget(metrics):
                return ContentionDecision(
                    level=ContentionLevel.LOW,
                    action="throttle",
                    details="Scheduler over resource budget, throttling",
                    throttle_ms=self._budget.throttle_delay_ms
                )
            return ContentionDecision(level=ContentionLevel.NONE, action="proceed")

        # Interactive user active — check model contention
        if scheduler_model:
            interactive_model = await self._get_interactive_model()
            if interactive_model and interactive_model == scheduler_model:
                return ContentionDecision(
                    level=ContentionLevel.MODEL,
                    action="pause_node",
                    details=f"Model contention on {scheduler_model}"
                )

        # Interactive active but no model clash
        return ContentionDecision(
            level=ContentionLevel.LOW,
            action="proceed",
            details="Interactive active but using different model"
        )

    def get_allowed_mode(self) -> str:
        """Determine allowed scheduler mode based on current time."""
        now = datetime.now(self._tz)
        if self._is_business_hours(now):
            return self._bh_config.during_business_hours_mode
        return self._bh_config.outside_business_hours_mode

    def _is_business_hours(self, local_now: datetime) -> bool:
        """Check if current time is within business hours."""
        if local_now.weekday() not in self._bh_config.business_days:
            return False

        start_str, end_str = self._bh_config.business_hours.split("-")
        start = dt_time.fromisoformat(start_str)
        end = dt_time.fromisoformat(end_str)
        current = local_now.time()

        return start <= current <= end

    def _is_over_budget(self, metrics) -> bool:
        """Check if scheduler is consuming more than its budget."""
        return (
            metrics.cpu_pct > self._budget.max_cpu_pct or
            metrics.ram_pct > self._budget.max_ram_pct or
            metrics.gpu_pct > self._budget.max_gpu_pct
        )

    async def _detect_interactive_activity(self) -> bool:
        """Detect any form of interactive user activity."""
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(minutes=5)

        # Method 1: Active SSE connections
        sse_count = await self._db["sse_connections"].count_documents({"active": True})
        if sse_count > 0:
            return True

        # Method 2: Recent chat API calls
        chat_count = await self._db["activity_logs"].count_documents({
            "type": "chat_request",
            "timestamp": {"$gte": cutoff}
        })
        if chat_count > 0:
            return True

        # Method 3: Active ingestion jobs
        ingestion_count = await self._db["ingestion_jobs"].count_documents({
            "status": "running"
        })
        if ingestion_count > 0:
            return True

        return False

    async def _get_interactive_model(self) -> Optional[str]:
        """Determine which model interactive users are currently using."""
        # Query most recent interactive inference
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(seconds=30)

        recent = await self._db["activity_logs"].find_one(
            {"type": "llm_inference", "timestamp": {"$gte": cutoff}},
            sort=[("timestamp", -1)]
        )
        if recent:
            return recent.get("model")
        return None
```

---

## 7. Configuration Example

Complete configuration combining all sections:

```yaml
# strategy_schedule document example
schedule:
  id: "overnight-legal-core-v2"
  name: "Quellex Legal Core Overnight Exploration"
  enabled: true
  tenant: "quellex"
  profile_key: "rag_test_law"
  schedule_type: "cron"
  cron: "0 22 * * 1-5"  # 22:00 Mon-Fri
  timezone: "Europe/Vienna"
  allowed_window: "22:00-06:00"
  mode: "exploration"

  # Timezone/DST (Section 1)
  timezone_config:
    skip_nonexistent_hours: true
    dedup_ambiguous_hours: true

  # Pause/Resume (Section 2)
  pause_config:
    max_pause_minutes: 120
    check_interval_seconds: 60
    pause_if_interactive_users: true
    pause_if_backend_chat_active: true
    pause_if_resource_pressure: true
    resource_thresholds:
      cpu_pct: 80
      ram_pct: 85
      gpu_pct: 90

  # Stop conditions (Section 3)
  stop_conditions:
    max_runtime_minutes: 360
    max_llm_calls: 500
    max_cost_usd: 50.0
    stop_on_leader_found: true
    leader_margin_pct: 10.0
    stop_on_fatal_failure_rate: 0.5
    min_candidates_completed: 3
    max_consecutive_failures: 5

  # Resource budgets (Section 6)
  scheduler_budget:
    max_cpu_pct: 60
    max_ram_pct: 50
    max_gpu_pct: 70
    throttle_delay_ms: 2000

  # Business hours (Section 6)
  business_hours_config:
    business_hours: "08:00-18:00"
    business_days: [0, 1, 2, 3, 4]
    during_business_hours_mode: "smoke"
    outside_business_hours_mode: "exploration"

  # Ollama (Section 4)
  ollama_config:
    poll_interval_seconds: 30
    max_wait_ms: 30000
    keep_alive_override: "30m"
    thrashing_threshold: 5
    thrashing_window_minutes: 10
    prewarm_first_n_candidates: 2

  # MongoDB resilience (Section 5)
  db_resilience:
    spec_cache_ttl_seconds: 300
    result_buffer_max_size: 100
    result_buffer_flush_interval_seconds: 30
    circuit_breaker:
      failure_threshold: 3
      failure_window_seconds: 60
      recovery_timeout_seconds: 30
```

---

## 8. Integration Points

| Component | Blueprint | Integration |
|---|---|---|
| ScheduleTimezoneResolver | 05 §4 (Schedule model) | Wraps cron evaluation with DST safety |
| PauseConditionEvaluator | 05 §9 (Scheduler daemon) | Called in main scheduler loop |
| StopConditionEvaluator | 05 §5 (Resource limits) | Extends basic limits with smart stops |
| OllamaResidencyManager | 08 §10 (Model prewarming) | Implements residency tracking and priority |
| MongoDBCircuitBreaker | 05 §8 (Job lifecycle) | Protects all DB operations in scheduler |
| ResourceContentionManager | 08 §7 (Adaptive strategy) | Controls scheduler resource usage |

---

## 9. Acceptance Criteria

- [ ] Schedule runs respect IANA timezones and handle DST spring-forward/fall-back correctly
- [ ] Scheduler pauses when interactive users are detected and resumes when activity clears
- [ ] Max pause duration causes deferral, not infinite waiting
- [ ] Partial experiment results are preserved on pause timeout
- [ ] Stop conditions halt experiments early with appropriate reason codes
- [ ] Cost estimation works for both cloud and local (zero-cost) models
- [ ] Leader detection requires minimum candidates before triggering
- [ ] Ollama model residency is tracked and concurrent access is coordinated
- [ ] Interactive requests preempt scheduler model access
- [ ] Model thrashing is detected and mitigated via candidate sorting
- [ ] MongoDB failures are handled via circuit breaker without data loss (within buffer limits)
- [ ] Strategy spec cache serves stale data during DB outage
- [ ] Scheduler does not queue missed schedules for bulk retry
- [ ] Resource contention favors interactive users at all contention levels
- [ ] Business hours mode restricts scheduler to smoke tests during work hours
- [ ] All classes are testable in isolation with injected dependencies
