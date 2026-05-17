# Blueprint 13C — DAG Resilience, Streaming, and PII Handling

> Resolves specification gaps: **B2-1** (intermediate state persistence), **B2-2** (timeout/cancellation semantics), **B2-3** (SSE streaming integration), **B2-4** (evidence card PII), **EC-1** (empty profile handling), **EC-2** (embedding dimension mismatch), **EC-5** (disk space protection).

---

## 1. Intermediate State Persistence and DAG Resumability

### 1.1 Design Philosophy

The strategy runner operates in two persistence modes based on strategy budget:

| Condition | Mode | Rationale |
|---|---|---|
| `latency_hard_limit_ms <= 60_000` | **In-memory only** | Fast path; checkpoint overhead exceeds benefit |
| `latency_hard_limit_ms > 60_000` | **Checkpoint-persisted** | Long-running strategies survive process restarts |

### 1.2 Checkpoint Collection

**Collection:** `strategy_run_checkpoints`

**TTL Index:** `created_at` with `expireAfterSeconds: 3600` (1-hour auto-expire)

```javascript
db.strategy_run_checkpoints.createIndex(
  { "created_at": 1 },
  { expireAfterSeconds: 3600 }
)
db.strategy_run_checkpoints.createIndex(
  { "run_id": 1 },
  { unique: true }
)
db.strategy_run_checkpoints.createIndex(
  { "session_id": 1, "cancelled": 1 }
)
```

### 1.3 Checkpoint Schema

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class StrategyRunCheckpoint(BaseModel):
    """Persisted snapshot of a DAG execution for crash recovery."""

    run_id: str = Field(..., description="Unique execution run identifier")
    trace_id: str = Field(..., description="Distributed tracing correlation ID")
    strategy_id: str = Field(..., description="Strategy spec ID being executed")
    strategy_version: str = Field(..., description="Strategy spec version")
    session_id: str = Field(..., description="User chat session ID")
    user_id: str = Field(..., description="Owning user")

    state_snapshot: dict = Field(
        ...,
        description="Serialized StrategyRunState (context, intermediate results, "
                    "accumulated evidence cards, budget consumption)"
    )
    last_completed_node: str = Field(
        ...,
        description="Node ID of the most recently completed node"
    )
    completed_nodes: list[str] = Field(
        default_factory=list,
        description="Ordered list of all completed node IDs"
    )
    pending_nodes: list[str] = Field(
        default_factory=list,
        description="Remaining nodes in topological order"
    )

    cancelled: bool = Field(default=False, description="Cancellation flag")
    cancel_reason: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "run_id": "run_abc123",
                "trace_id": "trace_xyz789",
                "strategy_id": "legal_deep_contradiction_analysis",
                "strategy_version": "2.1.0",
                "session_id": "sess_456",
                "user_id": "user_001",
                "state_snapshot": {"evidence_cards": [], "budget_consumed_ms": 34000},
                "last_completed_node": "retrieve_concept",
                "completed_nodes": ["normalize", "context", "retrieve_exact", "retrieve_concept"],
                "pending_nodes": ["filter", "cards", "synthesize", "validate", "emit_telemetry"],
                "cancelled": False,
                "created_at": "2026-05-17T10:00:00Z",
                "updated_at": "2026-05-17T10:00:34Z",
            }
        }
```

### 1.4 Checkpoint Lifecycle

```text
┌──────────────┐     node completes      ┌─────────────────┐
│  Runner      │ ─────────────────────►   │  Upsert         │
│  executes    │                          │  checkpoint     │
│  node N      │                          │  (run_id key)   │
└──────────────┘                          └─────────────────┘
                                                   │
                                          TTL expires (1h)
                                                   ▼
                                          ┌─────────────────┐
                                          │  Auto-deleted   │
                                          │  by MongoDB     │
                                          └─────────────────┘
```

### 1.5 Checkpoint Write Algorithm

```python
async def maybe_checkpoint(
    self,
    state: "StrategyRunState",
    completed_node_id: str,
) -> None:
    """Persist checkpoint if strategy qualifies for persistence."""
    if state.spec.budgets.latency_hard_limit_ms <= 60_000:
        return  # fast-path: no persistence overhead

    checkpoint = StrategyRunCheckpoint(
        run_id=state.run_id,
        trace_id=state.trace_id,
        strategy_id=state.spec.id,
        strategy_version=state.spec.version,
        session_id=state.session_id,
        user_id=state.user_id,
        state_snapshot=state.serialize(),
        last_completed_node=completed_node_id,
        completed_nodes=state.completed_node_ids,
        pending_nodes=state.remaining_node_ids,
    )

    await self._db.strategy_run_checkpoints.replace_one(
        {"run_id": state.run_id},
        checkpoint.model_dump(),
        upsert=True,
    )
```

### 1.6 Resume Logic

On process start (lifespan startup), the runner checks for orphaned checkpoints:

```python
async def recover_orphaned_runs(self) -> list[str]:
    """Find and resume orphaned strategy runs from checkpoints.

    Called during FastAPI lifespan startup.
    Returns list of resumed run_ids.
    """
    orphaned = await self._db.strategy_run_checkpoints.find(
        {"cancelled": False}
    ).to_list(length=50)

    resumed = []
    for doc in orphaned:
        checkpoint = StrategyRunCheckpoint(**doc)
        state = StrategyRunState.deserialize(
            checkpoint.state_snapshot,
            resume_from=checkpoint.last_completed_node,
        )
        # Re-enqueue for execution from next node
        asyncio.create_task(
            self._resume_strategy(state, checkpoint)
        )
        resumed.append(checkpoint.run_id)

    if resumed:
        logger.info(f"Resumed {len(resumed)} orphaned strategy runs: {resumed}")
    return resumed
```

### 1.7 Cancellation via Checkpoint

```python
async def cancel_run(self, run_id: str, reason: str = "user_cancelled") -> bool:
    """Mark a checkpoint as cancelled. Runner checks before each node dispatch."""
    result = await self._db.strategy_run_checkpoints.update_one(
        {"run_id": run_id, "cancelled": False},
        {"$set": {"cancelled": True, "cancel_reason": reason, "updated_at": datetime.utcnow()}}
    )
    return result.modified_count > 0
```

The runner checks cancellation before dispatching each node:

```python
async def _check_cancelled(self, state: "StrategyRunState") -> bool:
    """Check if run has been cancelled (only for persisted runs)."""
    if state.spec.budgets.latency_hard_limit_ms <= 60_000:
        return state.cancelled  # in-memory flag only

    doc = await self._db.strategy_run_checkpoints.find_one(
        {"run_id": state.run_id},
        projection={"cancelled": 1, "cancel_reason": 1}
    )
    if doc and doc.get("cancelled"):
        state.cancelled = True
        state.cancel_reason = doc.get("cancel_reason")
        return True
    return False
```

---

## 2. Timeout and Cancellation Semantics

### 2.1 Node-Level Timeout

Each node can define `timeout_ms` in the StrategySpec graph:

```yaml
nodes:
  - id: synthesize
    type: synthesize
    timeout_ms: 30000
    model_role: orchestrator_compact
```

**Timeout behavior:**

```python
from backend.agent.models import NodeOutput


async def execute_node_with_timeout(
    self,
    node: "StrategyNode",
    state: "StrategyRunState",
) -> NodeOutput:
    """Execute a single node with configurable timeout."""
    timeout_ms = node.timeout_ms or self._default_timeout_ms(node.type)
    timeout_s = timeout_ms / 1000.0

    try:
        async with asyncio.timeout(timeout_s):
            result = await self._node_registry.execute(node, state)
            return result
    except asyncio.TimeoutError:
        # Check for partial result from streaming LLM call
        partial = state.get_partial_result(node.id)
        if partial:
            return NodeOutput(
                node_id=node.id,
                success=True,
                timed_out=True,
                output=partial,
                duration_ms=timeout_ms,
                message=f"Node timed out after {timeout_ms}ms; partial result used",
            )
        else:
            return NodeOutput(
                node_id=node.id,
                success=False,
                timed_out=True,
                output=None,
                duration_ms=timeout_ms,
                message=f"Node timed out after {timeout_ms}ms; no partial result available",
            )
```

**Default timeout by node type:**

| Node Type | Default Timeout (ms) | Rationale |
|---|---:|---|
| `normalize_query` | 5 000 | Simple text transform |
| `business_context` | 3 000 | Config lookup |
| `intent_classify` | 5 000 | Small model |
| `retrieve` | 15 000 | Network + DB |
| `query_expand` | 10 000 | LLM call |
| `rerank` | 10 000 | Cross-encoder or LLM |
| `dedupe_versions` | 3 000 | In-memory filter |
| `boilerplate_filter` | 3 000 | In-memory filter |
| `evidence_cards` | 20 000 | LLM generation |
| `plan` | 15 000 | LLM reasoning |
| `synthesize` | 45 000 | Primary generation |
| `validate_citations` | 10 000 | LLM verification |
| `validate_contract` | 5 000 | Deterministic check |
| `judge_quality` | 15 000 | LLM evaluation |
| `refine` | 30 000 | LLM re-generation |
| `compare_candidates` | 10 000 | LLM comparison |
| `emit_telemetry` | 5 000 | Async write |

### 2.2 Strategy-Level Timeout (`latency_hard_limit_ms`)

The runner continuously tracks elapsed time and makes budget-aware dispatch decisions:

```python
async def run(self, context: BusinessContext, spec: StrategySpec) -> AgentResponseEnvelope:
    state = StrategyRunState(context=context, spec=spec)
    graph = self.compile_graph(spec.graph)
    start_time = time.monotonic()

    for node in graph.execution_order():
        # --- Budget check ---
        elapsed_ms = (time.monotonic() - start_time) * 1000
        remaining_budget = spec.budgets.latency_hard_limit_ms - elapsed_ms

        if remaining_budget <= 0:
            state.halt_reason = "latency_hard_limit_exceeded"
            break

        estimated_duration = self._estimate_node_duration(node, state)

        if remaining_budget < estimated_duration and node.config.get("optional", False):
            state.record_node_skipped(node.id, reason="budget_exhausted")
            continue

        if remaining_budget < estimated_duration and not node.config.get("optional", False):
            # Dispatch anyway but with reduced timeout
            node_timeout = max(int(remaining_budget), 1000)
        else:
            node_timeout = node.timeout_ms or self._default_timeout_ms(node.type)

        # --- Cancellation check ---
        if await self._check_cancelled(state):
            state.halt_reason = "cancelled"
            break

        # --- Execute ---
        result = await self.execute_node_with_timeout(node, state)
        state.record_node_result(node.id, result)
        await self.maybe_checkpoint(state, node.id)

        if result.halt:
            break

    return self.build_response_envelope(state)
```

**Timeout Decision Tree:**

```text
Before dispatching Node N:
│
├─ remaining_budget <= 0?
│   └─ YES → HALT strategy, return best-effort partial result
│
├─ remaining_budget < estimated_duration?
│   ├─ node.optional == true?
│   │   └─ YES → SKIP node, record "budget_exhausted"
│   │   └─ NO  → DISPATCH with timeout = remaining_budget
│   │
│   └─ (not optional, dispatch with reduced timeout)
│
└─ remaining_budget >= estimated_duration?
    └─ DISPATCH normally with configured/default timeout
```

### 2.3 Estimated Duration Resolution

```python
def _estimate_node_duration(self, node: "StrategyNode", state: "StrategyRunState") -> float:
    """Estimate expected duration for budget-aware scheduling.

    Resolution order:
    1. Runtime model profiles (historical p90 from telemetry)
    2. Node-type default table
    3. Configured timeout_ms as upper bound
    """
    # 1. Historical runtime data
    if state.runtime_profiles and node.id in state.runtime_profiles:
        return state.runtime_profiles[node.id].p90_duration_ms

    # 2. Node-type default
    return self._default_timeout_ms(node.type) * 0.5  # estimate at 50% of max
```

### 2.4 User Cancellation

**Detection:** The SSE connection from the client is monitored. When the client disconnects (closes the EventSource or navigates away), the FastAPI `Request.is_disconnected()` check fires.

**Cancellation State Machine:**

```text
         ┌────────────┐
         │  RUNNING   │
         └─────┬──────┘
               │
    client disconnects / explicit cancel
               │
               ▼
    ┌───────────────────┐
    │  CANCELLING       │
    │  (grace period:   │
    │   current node    │
    │   has 5s to       │
    │   finish)         │
    └─────┬─────────────┘
          │
          │  node finishes OR 5s grace expires
          │
          ▼
    ┌───────────────────┐
    │  CANCELLED        │
    │  (partial result  │
    │   returned)       │
    └───────────────────┘
```

**Implementation:**

```python
async def handle_user_cancellation(
    self,
    state: "StrategyRunState",
    current_node_task: Optional[asyncio.Task],
) -> None:
    """Handle user-initiated cancellation via SSE disconnect."""
    state.cancelled = True
    state.cancel_reason = "user_disconnected"

    if current_node_task and not current_node_task.done():
        # Grace period: let current node finish within 5 seconds
        try:
            await asyncio.wait_for(current_node_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            current_node_task.cancel()

    # Record cancellation telemetry
    await self._emit_cancellation_telemetry(state)
```

**Telemetry for cancellation:**

```python
class CancellationEvent(BaseModel):
    run_id: str
    strategy_id: str
    session_id: str
    elapsed_ms: int
    last_completed_node: str
    completed_node_count: int
    total_node_count: int
    cancel_reason: str  # "user_disconnected" | "admin_cancelled" | "system_timeout"
    partial_result_available: bool
```

---

## 3. SSE Streaming Integration with DAG Runner

### 3.1 SSE Event Type Catalog

All events are JSON-encoded and sent as `data:` frames per the SSE specification.

#### `strategy_started`

Emitted once at the start of DAG execution.

```json
{
  "type": "strategy_started",
  "strategy_id": "legal_hearing_questions_fast_evidence",
  "strategy_name": "Legal Hearing Questions — Fast Evidence",
  "strategy_version": "1.0.0",
  "node_count": 9,
  "estimated_duration_ms": 22000,
  "run_id": "run_abc123",
  "trace_id": "trace_xyz789"
}
```

#### `node_started`

Emitted when a node begins execution.

```json
{
  "type": "node_started",
  "node_id": "retrieve_exact",
  "node_type": "retrieve",
  "position": "3/9",
  "model_role": null,
  "timeout_ms": 15000
}
```

#### `node_complete`

Emitted when a node finishes (success, skip, timeout, or error).

```json
{
  "type": "node_complete",
  "node_id": "retrieve_exact",
  "node_type": "retrieve",
  "duration_ms": 2340,
  "status": "success",
  "output_summary": "Found 12 chunks from 4 documents",
  "tokens_used": 0,
  "position": "3/9"
}
```

**Status values:** `"success"` | `"skipped"` | `"timed_out"` | `"error"`

#### `evidence_cards_ready`

Emitted after the `evidence_cards` node completes.

```json
{
  "type": "evidence_cards_ready",
  "card_count": 14,
  "card_types": ["legal_question_card"],
  "topics": ["Mietminderung", "Schönheitsreparaturen", "Betriebskosten"]
}
```

#### `synthesis_token`

Emitted during streaming token generation from the `synthesize` node.

```json
{
  "type": "synthesis_token",
  "content": "Basierend auf den vorliegenden Unterlagen"
}
```

#### `validation_complete`

Emitted after all validation gates have run.

```json
{
  "type": "validation_complete",
  "passed": true,
  "failures": [],
  "validators_run": ["answer_contract_validator", "citation_coverage_validator"],
  "citation_coverage": 0.92
}
```

#### `strategy_complete`

Terminal event for successful strategy execution.

```json
{
  "type": "strategy_complete",
  "strategy_id": "legal_hearing_questions_fast_evidence",
  "run_id": "run_abc123",
  "total_duration_ms": 18420,
  "success": true,
  "evidence_card_count": 14,
  "validation_passed": true,
  "nodes_executed": 9,
  "nodes_skipped": 0,
  "budget_consumed_pct": 41
}
```

#### `strategy_error`

Terminal event for failed strategy execution.

```json
{
  "type": "strategy_error",
  "error_type": "latency_hard_limit_exceeded",
  "message": "Strategy exceeded 45000ms budget at node synthesize (elapsed: 45230ms)",
  "halt_reason": "budget",
  "last_completed_node": "cards",
  "partial_result_available": true
}
```

### 3.2 Integration with Current SSE System

The existing SSE implementation in `backend/routers/sessions.py` emits:

- `start` — session/model metadata
- `phase` — coarse phases (`analyze`, `plan`, `execute`, `synthesize`)
- `orchestrator_step` — orchestrator reasoning
- `worker_step` — worker search/tool results
- `response` — final response with trace
- `error` — error message

**Legacy Adapter (dual emission):**

During the migration period, the strategy runner emits **both** legacy phase events and new node-level events:

```python
class LegacySSEAdapter:
    """Emits both old-style phase events and new node events for backward compatibility."""

    NODE_TO_PHASE_MAP: dict[str, str] = {
        "normalize_query": "analyze",
        "business_context": "analyze",
        "intent_classify": "analyze",
        "retrieve": "execute",
        "query_expand": "execute",
        "rerank": "execute",
        "dedupe_versions": "execute",
        "boilerplate_filter": "execute",
        "evidence_cards": "execute",
        "plan": "plan",
        "synthesize": "synthesize",
        "validate_citations": "synthesize",
        "validate_contract": "synthesize",
        "judge_quality": "synthesize",
        "refine": "synthesize",
        "compare_candidates": "synthesize",
        "emit_telemetry": "synthesize",
    }

    async def emit_node_started(self, node: "StrategyNode", state: "StrategyRunState"):
        phase = self.NODE_TO_PHASE_MAP.get(node.type, "execute")
        current_phase = state.current_phase

        # Emit phase transition if phase changed
        if phase != current_phase:
            state.current_phase = phase
            await self._emit({"type": "phase", "phase": phase, "status": "started"})

        # Always emit new-style node event
        position = f"{state.completed_count + 1}/{state.total_node_count}"
        await self._emit({
            "type": "node_started",
            "node_id": node.id,
            "node_type": node.type,
            "position": position,
            "model_role": node.model_role,
            "timeout_ms": node.timeout_ms,
        })
```

**Migration path:**

1. **Phase 1 (current):** Legacy adapter emits both event styles. Existing frontend consumes `phase` events unchanged.
2. **Phase 2:** Frontend adds DAG progress UI consuming `node_started`/`node_complete`. Legacy `phase` events still emitted.
3. **Phase 3:** Remove legacy phase emission once all clients updated.

### 3.3 CLI Mode

```text
$ recallhub strategy run legal_hearing_questions_fast_evidence --query "..."

[██████░░░░░░░░░░] 3/9  retrieve_exact  (2.3s)
  ✓ normalize_query      0.1s
  ✓ business_context     0.05s
  ◐ retrieve_exact       ...
```

**`--no-stream` flag:** Suppresses all SSE-style progress; outputs only the final JSON result:

```text
$ recallhub strategy run --no-stream --query "..."
{"success": true, "answer": "...", "evidence_cards": [...], ...}
```

### 3.4 Example SSE Event Sequence

Complete event flow for a 9-node strategy:

```text
data: {"type":"strategy_started","strategy_id":"legal_hearing_questions_fast_evidence","strategy_name":"Legal Hearing Questions — Fast Evidence","node_count":9,"estimated_duration_ms":22000,"run_id":"run_f8a2","trace_id":"tr_991b"}

data: {"type":"node_started","node_id":"normalize","node_type":"normalize_query","position":"1/9","model_role":null,"timeout_ms":5000}

data: {"type":"node_complete","node_id":"normalize","node_type":"normalize_query","duration_ms":85,"status":"success","output_summary":"Query normalized","tokens_used":0,"position":"1/9"}

data: {"type":"node_started","node_id":"context","node_type":"business_context","position":"2/9","model_role":null,"timeout_ms":3000}

data: {"type":"node_complete","node_id":"context","node_type":"business_context","duration_ms":42,"status":"success","output_summary":"Context loaded: quellex/legal","tokens_used":0,"position":"2/9"}

data: {"type":"node_started","node_id":"retrieve_exact","node_type":"retrieve","position":"3/9","model_role":null,"timeout_ms":15000}

data: {"type":"node_complete","node_id":"retrieve_exact","node_type":"retrieve","duration_ms":2340,"status":"success","output_summary":"Found 12 chunks from 4 documents","tokens_used":0,"position":"3/9"}

data: {"type":"node_started","node_id":"retrieve_concept","node_type":"retrieve","position":"4/9","model_role":null,"timeout_ms":15000}

data: {"type":"node_complete","node_id":"retrieve_concept","node_type":"retrieve","duration_ms":1890,"status":"success","output_summary":"Found 8 chunks from 3 documents","tokens_used":0,"position":"4/9"}

data: {"type":"node_started","node_id":"filter","node_type":"dedupe_versions","position":"5/9","model_role":null,"timeout_ms":3000}

data: {"type":"node_complete","node_id":"filter","node_type":"dedupe_versions","duration_ms":120,"status":"success","output_summary":"Deduped: 20→15 chunks","tokens_used":0,"position":"5/9"}

data: {"type":"node_started","node_id":"cards","node_type":"evidence_cards","position":"6/9","model_role":"worker_fast","timeout_ms":20000}

data: {"type":"node_complete","node_id":"cards","node_type":"evidence_cards","duration_ms":8450,"status":"success","output_summary":"Generated 14 evidence cards","tokens_used":3200,"position":"6/9"}

data: {"type":"evidence_cards_ready","card_count":14,"card_types":["legal_question_card"],"topics":["Mietminderung","Schönheitsreparaturen","Betriebskosten"]}

data: {"type":"node_started","node_id":"synthesize","node_type":"synthesize","position":"7/9","model_role":"orchestrator_compact","timeout_ms":45000}

data: {"type":"synthesis_token","content":"Basierend auf"}
data: {"type":"synthesis_token","content":" den vorliegenden"}
data: {"type":"synthesis_token","content":" Unterlagen ergeben sich"}
data: {"type":"synthesis_token","content":" folgende Fragen..."}

data: {"type":"node_complete","node_id":"synthesize","node_type":"synthesize","duration_ms":12300,"status":"success","output_summary":"Generated 1400-token response","tokens_used":1400,"position":"7/9"}

data: {"type":"node_started","node_id":"validate","node_type":"validate_contract","position":"8/9","model_role":null,"timeout_ms":5000}

data: {"type":"node_complete","node_id":"validate","node_type":"validate_contract","duration_ms":180,"status":"success","output_summary":"Contract validated","tokens_used":0,"position":"8/9"}

data: {"type":"validation_complete","passed":true,"failures":[],"validators_run":["answer_contract_validator","citation_coverage_validator"],"citation_coverage":0.92}

data: {"type":"node_started","node_id":"emit_telemetry","node_type":"emit_telemetry","position":"9/9","model_role":null,"timeout_ms":5000}

data: {"type":"node_complete","node_id":"emit_telemetry","node_type":"emit_telemetry","duration_ms":95,"status":"success","output_summary":"Telemetry persisted","tokens_used":0,"position":"9/9"}

data: {"type":"strategy_complete","strategy_id":"legal_hearing_questions_fast_evidence","run_id":"run_f8a2","total_duration_ms":25502,"success":true,"evidence_card_count":14,"validation_passed":true,"nodes_executed":9,"nodes_skipped":0,"budget_consumed_pct":57}
```

---

## 4. Evidence Card PII Handling

### 4.1 Dual-Use Principle

Evidence cards serve two distinct purposes with different privacy requirements:

| Context | PII Treatment | Rationale |
|---|---|---|
| In-memory (synthesis input) | **Original text retained** | LLM needs real names/entities for accurate synthesis |
| Telemetry persistence | **Pseudonymized** | Privacy protection for stored analytics data |
| Judge evaluation (local_only) | **Pseudonymized** | Judge must not memorize PII |
| Judge evaluation (cloud) | **Pseudonymized** | PII must never leave the system |

### 4.2 Field-Level Privacy Classification

```python
from enum import Enum
from typing import Optional


class PrivacyClass(str, Enum):
    """Privacy classification for evidence card fields."""
    PII_SENSITIVE = "pii_sensitive"       # Contains PII; must be pseudonymized or omitted
    METADATA_SAFE = "metadata_safe"       # Non-PII metadata; safe to persist as-is
    STRUCTURAL = "structural"             # Structural/positional data; always safe


class EvidenceCardTelemetry(BaseModel):
    """Evidence card representation for telemetry storage.

    Field-level privacy classification determines handling per privacy mode.
    """

    # --- STRUCTURAL (always stored) ---
    card_id: str                                    # privacy: STRUCTURAL
    card_type: str                                  # privacy: METADATA_SAFE
    topic: str                                      # privacy: METADATA_SAFE
    confidence: float                               # privacy: METADATA_SAFE
    used_in_final_answer: bool                      # privacy: STRUCTURAL
    final_answer_claim_ids: list[str] = []          # privacy: STRUCTURAL

    # --- METADATA_SAFE (always stored) ---
    source_id: str                                  # privacy: METADATA_SAFE
    document_title: str                             # privacy: METADATA_SAFE (titles are metadata)
    chunk_id: Optional[str] = None                  # privacy: METADATA_SAFE

    # --- PII_SENSITIVE (mode-dependent) ---
    source_excerpt: Optional[str] = None            # privacy: PII_SENSITIVE
    source_span_start: Optional[int] = None         # privacy: STRUCTURAL (offset only)
    source_span_end: Optional[int] = None           # privacy: STRUCTURAL (offset only)
    factual_basis: Optional[str] = None             # privacy: PII_SENSITIVE
    legal_or_technical_issue: Optional[str] = None  # privacy: METADATA_SAFE
    proposed_question: Optional[str] = None         # privacy: PII_SENSITIVE

    class Config:
        json_schema_extra = {
            "privacy_annotations": {
                "card_id": "STRUCTURAL",
                "card_type": "METADATA_SAFE",
                "topic": "METADATA_SAFE",
                "confidence": "METADATA_SAFE",
                "source_excerpt": "PII_SENSITIVE",
                "document_title": "METADATA_SAFE",
                "source_span_start": "STRUCTURAL",
                "source_span_end": "STRUCTURAL",
                "factual_basis": "PII_SENSITIVE",
                "proposed_question": "PII_SENSITIVE",
            }
        }
```

### 4.3 Privacy Mode Behavior

```python
from backend.services.pii_pseudonymizer import TelemetryPseudonymizer


class EvidenceCardSanitizer:
    """Applies privacy-mode-dependent sanitization to evidence cards before telemetry persistence."""

    def __init__(self, pseudonymizer: Optional[TelemetryPseudonymizer], raw_allowed: bool):
        self._pseudonymizer = pseudonymizer
        self._raw_allowed = raw_allowed

    def sanitize_for_telemetry(self, card: "EvidenceCard") -> EvidenceCardTelemetry:
        """Transform an in-memory evidence card into its telemetry representation."""

        if self._raw_allowed:
            # Dev/trusted-beta: store full cards with original text
            return EvidenceCardTelemetry(
                card_id=card.card_id,
                card_type=card.card_type,
                topic=card.topic,
                confidence=card.confidence,
                used_in_final_answer=card.used_in_final_answer,
                final_answer_claim_ids=card.final_answer_claim_ids,
                source_id=card.source_id,
                document_title=card.document_title,
                chunk_id=card.chunk_id,
                source_excerpt=card.source_excerpt,  # full text retained
                source_span_start=card.source_span.start if card.source_span else None,
                source_span_end=card.source_span.end if card.source_span else None,
                factual_basis=card.factual_basis,
                legal_or_technical_issue=card.legal_or_technical_issue,
                proposed_question=card.proposed_question,
            )
        else:
            # Production: pseudonymize PII-sensitive fields; omit source_excerpt entirely
            return EvidenceCardTelemetry(
                card_id=card.card_id,
                card_type=card.card_type,
                topic=card.topic,
                confidence=card.confidence,
                used_in_final_answer=card.used_in_final_answer,
                final_answer_claim_ids=card.final_answer_claim_ids,
                source_id=card.source_id,
                document_title=card.document_title,
                chunk_id=card.chunk_id,
                source_excerpt=None,  # OMITTED in production
                source_span_start=card.source_span.start if card.source_span else None,
                source_span_end=card.source_span.end if card.source_span else None,
                factual_basis=self._pseudonymize(card.factual_basis),
                legal_or_technical_issue=card.legal_or_technical_issue,
                proposed_question=self._pseudonymize(card.proposed_question),
            )

    def sanitize_for_judge(self, card: "EvidenceCard", local_only: bool) -> dict:
        """Sanitize evidence card for judge input.

        In local_only mode, pseudonymize to prevent local model memorization.
        In cloud mode, always pseudonymize.
        """
        return {
            "card_type": card.card_type,
            "topic": card.topic,
            "confidence": card.confidence,
            "source_excerpt": self._pseudonymize(card.source_excerpt),
            "factual_basis": self._pseudonymize(card.factual_basis),
            "document_title": card.document_title,  # metadata, not PII
        }

    def _pseudonymize(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        if self._pseudonymizer is None:
            return text
        return self._pseudonymizer.pseudonymize_text(text)
```

### 4.4 Privacy Decision Table

| Field | `raw_telemetry_allowed=true` | `raw_telemetry_allowed=false` | Judge Input |
|---|---|---|---|
| `card_id` | stored | stored | not sent |
| `card_type` | stored | stored | sent |
| `topic` | stored | stored | sent |
| `confidence` | stored | stored | sent |
| `document_title` | stored | stored | sent |
| `source_excerpt` | stored (original) | **omitted** | pseudonymized |
| `source_span` | start+end offsets | start+end offsets | not sent |
| `factual_basis` | stored (original) | pseudonymized | pseudonymized |
| `proposed_question` | stored (original) | pseudonymized | not sent |

---

## 5. Empty Profile Handling

### 5.1 Pre-Execution Validation

Before the strategy runner dispatches any node, it must validate that the target profile can support execution:

```python
from typing import Optional, Literal


class StrategyExecutabilityResult(BaseModel):
    """Result of pre-execution profile validation."""

    executable: bool
    reason: Optional[str] = None
    reason_code: Optional[Literal[
        "empty_profile",
        "no_vector_index",
        "vector_index_building",
        "embedding_dimension_mismatch",
        "profile_not_found",
    ]] = None
    user_message_de: Optional[str] = None
    user_message_en: Optional[str] = None
    telemetry_action: Optional[Literal[
        "strategy_skipped",
        "strategy_blocked",
    ]] = None
    detail: Optional[dict] = None


async def validate_strategy_executable_on_profile(
    strategy_spec: "StrategySpec",
    profile_key: str,
    db: "AsyncDatabase",
) -> StrategyExecutabilityResult:
    """Validate that a profile can support strategy execution.

    Checks performed:
    1. Profile document count > 0
    2. Profile has an active vector search index
    3. Profile embedding dimensions match strategy retrieval config
    """

    # --- Check 1: Document count ---
    chunks_collection = db[f"{profile_key}_chunks"]
    doc_count = await chunks_collection.count_documents({}, limit=1)

    if doc_count == 0:
        return StrategyExecutabilityResult(
            executable=False,
            reason="Profile has no ingested documents",
            reason_code="empty_profile",
            user_message_de=(
                "Keine Dokumente vorhanden. "
                "Bitte laden Sie zunächst Dokumente hoch."
            ),
            user_message_en=(
                "No documents available. "
                "Please ingest documents first."
            ),
            telemetry_action="strategy_skipped",
        )

    # --- Check 2: Vector index status ---
    try:
        indexes = await chunks_collection.list_search_indexes().to_list()
        vector_indexes = [
            idx for idx in indexes
            if idx.get("type") == "vectorSearch"
        ]

        if not vector_indexes:
            return StrategyExecutabilityResult(
                executable=False,
                reason="No vector search index found on chunks collection",
                reason_code="no_vector_index",
                user_message_de=(
                    "Suchindex fehlt. "
                    "Bitte kontaktieren Sie den Administrator."
                ),
                user_message_en=(
                    "Search index missing. "
                    "Please contact the administrator."
                ),
                telemetry_action="strategy_blocked",
            )

        # Check if any index is still building
        building = [idx for idx in vector_indexes if idx.get("status") == "BUILDING"]
        if building and not any(idx.get("status") == "READY" for idx in vector_indexes):
            return StrategyExecutabilityResult(
                executable=False,
                reason="Vector search index is still building",
                reason_code="vector_index_building",
                user_message_de=(
                    "Suchindex wird erstellt. "
                    "Bitte versuchen Sie es in wenigen Minuten erneut."
                ),
                user_message_en=(
                    "Search index is being created. "
                    "Please try again in a few minutes."
                ),
                telemetry_action="strategy_blocked",
            )
    except Exception as e:
        # If we cannot list indexes (permissions, Atlas tier), log and proceed
        logger.warning(f"Cannot verify vector index status: {e}")

    # --- Check 3: Embedding dimension compatibility ---
    # Delegated to EmbeddingCompatibilityValidator (Section 6)
    compat = await EmbeddingCompatibilityValidator.check(
        strategy_spec=strategy_spec,
        chunks_collection=chunks_collection,
    )
    if not compat.compatible:
        return StrategyExecutabilityResult(
            executable=False,
            reason=compat.message,
            reason_code="embedding_dimension_mismatch",
            user_message_de=(
                f"Embedding-Dimensionen stimmen nicht überein. "
                f"Erwartet: {compat.expected_dim}, Vorhanden: {compat.actual_dim}."
            ),
            user_message_en=(
                f"Embedding dimensions do not match. "
                f"Expected: {compat.expected_dim}, Found: {compat.actual_dim}."
            ),
            telemetry_action="strategy_blocked",
            detail={
                "expected_dim": compat.expected_dim,
                "actual_dim": compat.actual_dim,
                "chunk_count": compat.chunk_count,
            },
        )

    return StrategyExecutabilityResult(executable=True)
```

### 5.2 Integration Point

```python
# In StrategyRunner.run():
executability = await validate_strategy_executable_on_profile(
    strategy_spec=spec,
    profile_key=context.active_profile_key,
    db=self._db,
)

if not executability.executable:
    # Record telemetry
    await self._telemetry.record_strategy_skip(
        strategy_id=spec.id,
        session_id=state.session_id,
        reason_code=executability.reason_code,
        detail=executability.detail,
    )
    # Return user-facing response
    language = context.language or "de"
    message = (
        executability.user_message_de if language == "de"
        else executability.user_message_en
    )
    return AgentResponseEnvelope(
        success=False,
        response_text=message,
        strategy_skipped=True,
        skip_reason=executability.reason_code,
    )
```

---

## 6. Embedding Dimension Mismatch Detection

### 6.1 Known Dimension Map

```python
EMBEDDING_DIMENSION_MAP: dict[str, int] = {
    # OpenAI
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Local / Ollama
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "snowflake-arctic-embed:335m": 1024,
    "bge-large-en-v1.5": 1024,
    "bge-m3": 1024,
    # Cohere
    "embed-english-v3.0": 1024,
    "embed-multilingual-v3.0": 1024,
}
```

### 6.2 Compatibility Validator

```python
from typing import Optional
from pydantic import BaseModel


class EmbeddingCompatibilityResult(BaseModel):
    """Result of embedding dimension compatibility check."""
    compatible: bool
    expected_dim: Optional[int] = None
    actual_dim: Optional[int] = None
    expected_model: Optional[str] = None
    actual_model: Optional[str] = None
    chunk_count: int = 0
    message: Optional[str] = None


class EmbeddingCompatibilityValidator:
    """Validates that strategy embedding expectations match profile data.

    Performs both static (known dimension map) and dynamic (sample query)
    validation to detect mismatches before strategy execution or activation.
    """

    @classmethod
    async def check(
        cls,
        strategy_spec: "StrategySpec",
        chunks_collection: "AsyncCollection",
    ) -> EmbeddingCompatibilityResult:
        """Check embedding compatibility between strategy and stored chunks.

        Algorithm:
        1. Determine expected dimensions from strategy's retrieval_policy.embedding_model
        2. Sample one chunk from collection to get actual embedding dimensions
        3. Compare and report mismatch
        """
        # 1. Determine expected dimensions
        embedding_model = getattr(
            strategy_spec.retrieval_policy, "embedding_model", None
        )
        if not embedding_model:
            # Strategy doesn't specify model; skip check
            return EmbeddingCompatibilityResult(compatible=True)

        expected_dim = EMBEDDING_DIMENSION_MAP.get(embedding_model)
        if expected_dim is None:
            # Unknown model; cannot validate statically
            return EmbeddingCompatibilityResult(
                compatible=True,
                message=f"Unknown embedding model '{embedding_model}'; dimension check skipped",
            )

        # 2. Sample actual dimensions from stored chunks
        sample = await chunks_collection.find_one(
            {"embedding": {"$exists": True}},
            projection={"embedding": 1}
        )

        if sample is None:
            # No embeddings stored yet; cannot validate
            return EmbeddingCompatibilityResult(
                compatible=True,
                message="No embedded chunks found; dimension check deferred",
            )

        actual_embedding = sample.get("embedding", [])
        actual_dim = len(actual_embedding) if isinstance(actual_embedding, list) else 0

        # 3. Compare
        if actual_dim != expected_dim:
            chunk_count = await chunks_collection.count_documents(
                {"embedding": {"$exists": True}}
            )
            return EmbeddingCompatibilityResult(
                compatible=False,
                expected_dim=expected_dim,
                actual_dim=actual_dim,
                expected_model=embedding_model,
                chunk_count=chunk_count,
                message=(
                    f"Strategy requires {expected_dim}-dimensional embeddings "
                    f"(model: {embedding_model}) but profile uses "
                    f"{actual_dim}-dimensional embeddings. "
                    f"Re-embedding required for {chunk_count} chunks."
                ),
            )

        return EmbeddingCompatibilityResult(
            compatible=True,
            expected_dim=expected_dim,
            actual_dim=actual_dim,
            expected_model=embedding_model,
        )

    @classmethod
    async def check_on_activation(
        cls,
        strategy_spec: "StrategySpec",
        profile_key: str,
        db: "AsyncDatabase",
    ) -> EmbeddingCompatibilityResult:
        """Validate embedding compatibility during strategy activation/registration.

        Called when:
        - A strategy status transitions to 'active' or 'candidate'
        - A strategy is registered in the DB store
        - An admin activates a strategy via API

        Blocks activation on mismatch.
        """
        chunks_collection = db[f"{profile_key}_chunks"]
        result = await cls.check(strategy_spec, chunks_collection)

        if not result.compatible:
            logger.error(
                f"Strategy '{strategy_spec.id}' blocked from activation: {result.message}"
            )

        return result
```

### 6.3 Runtime Warning (Lightweight)

In addition to the blocking check at activation, a lightweight runtime warning is emitted if the strategy's configured model differs from the system's active embedding config:

```python
async def _warn_embedding_drift(self, spec: "StrategySpec", state: "StrategyRunState"):
    """Emit WARNING if strategy's embedding model differs from active system config.

    This is a non-blocking advisory check — the hard blocking happens at activation.
    """
    strategy_model = getattr(spec.retrieval_policy, "embedding_model", None)
    system_model = settings.embedding_model

    if strategy_model and system_model and strategy_model != system_model:
        logger.warning(
            f"Strategy '{spec.id}' expects embedding model '{strategy_model}' "
            f"but system is configured with '{system_model}'. "
            f"Retrieval results may be suboptimal."
        )
        state.warnings.append(
            f"Embedding model drift: strategy={strategy_model}, system={system_model}"
        )
```

---

## 7. Disk Space Protection During Overnight Exploration

### 7.1 Storage Estimates

| Data Type | Estimated Size per Strategy Run |
|---|---:|
| Node metrics (9 nodes) | 5–15 KB |
| Evidence cards (14 cards) | 20–80 KB |
| Validation results | 2–5 KB |
| Resource snapshots (5 points) | 3–8 KB |
| Raw telemetry record | 50–200 KB |
| **Total per run** | **~100–500 KB** |

For a 1000-run overnight exploration: **~100–500 MB**

### 7.2 DiskSpaceMonitor

```python
import shutil
import logging
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


logger = logging.getLogger(__name__)


class DiskSpaceStatus(BaseModel):
    """Current disk space assessment."""
    available_mb: float
    total_mb: float
    used_percent: float
    status: str  # "ok" | "warning" | "critical"
    message: Optional[str] = None


class DiskSpaceMonitor:
    """Monitors available disk space for telemetry data directory.

    Protects against disk exhaustion during long-running overnight experiments
    by checking space at configurable intervals and aborting gracefully.
    """

    def __init__(
        self,
        telemetry_path: str = "data/telemetry",
        min_available_mb: int = 500,
        critical_available_mb: int = 100,
        check_interval_runs: int = 10,
    ):
        """Initialize disk space monitor.

        Args:
            telemetry_path: Path to telemetry storage directory.
            min_available_mb: Warn threshold in MB (proceed but log WARNING).
            critical_available_mb: Abort threshold in MB (halt experiment).
            check_interval_runs: Check disk after every N strategy runs.
        """
        self._path = Path(telemetry_path)
        self._min_available_mb = min_available_mb
        self._critical_available_mb = critical_available_mb
        self._check_interval = check_interval_runs
        self._runs_since_check = 0

    def check(self) -> DiskSpaceStatus:
        """Check current disk space status.

        Returns:
            DiskSpaceStatus with current assessment.
        """
        try:
            usage = shutil.disk_usage(self._path)
            available_mb = usage.free / (1024 * 1024)
            total_mb = usage.total / (1024 * 1024)
            used_percent = (usage.used / usage.total) * 100

            if available_mb < self._critical_available_mb:
                status = "critical"
                message = (
                    f"CRITICAL: Only {available_mb:.0f} MB available "
                    f"(threshold: {self._critical_available_mb} MB). "
                    f"Experiment must abort."
                )
                logger.critical(message)
            elif available_mb < self._min_available_mb:
                status = "warning"
                message = (
                    f"WARNING: Only {available_mb:.0f} MB available "
                    f"(threshold: {self._min_available_mb} MB). "
                    f"Proceeding with caution."
                )
                logger.warning(message)
            else:
                status = "ok"
                message = None

            return DiskSpaceStatus(
                available_mb=available_mb,
                total_mb=total_mb,
                used_percent=used_percent,
                status=status,
                message=message,
            )
        except OSError as e:
            logger.error(f"Cannot check disk space: {e}")
            return DiskSpaceStatus(
                available_mb=0,
                total_mb=0,
                used_percent=100,
                status="critical",
                message=f"Disk space check failed: {e}",
            )

    def should_check(self) -> bool:
        """Whether a disk check is due based on run interval."""
        self._runs_since_check += 1
        if self._runs_since_check >= self._check_interval:
            self._runs_since_check = 0
            return True
        return False

    async def pre_experiment_check(self) -> DiskSpaceStatus:
        """Mandatory check before starting an experiment batch.

        Always runs regardless of interval.
        """
        status = self.check()
        if status.status == "critical":
            logger.critical(
                f"Pre-experiment disk check FAILED: {status.message}. "
                f"Experiment will not start."
            )
        return status
```

### 7.3 Integration with Experiment Runner

```python
class ExperimentRunner:
    """Orchestrates overnight batch strategy experiments."""

    async def run_experiment(
        self,
        experiment_id: str,
        strategy_specs: list["StrategySpec"],
        test_cases: list["TestCase"],
    ) -> "ExperimentResult":
        disk_monitor = DiskSpaceMonitor(
            telemetry_path=settings.telemetry_storage_path,
            min_available_mb=500,
            critical_available_mb=100,
            check_interval_runs=10,
        )

        # --- Pre-flight disk check ---
        pre_check = await disk_monitor.pre_experiment_check()
        if pre_check.status == "critical":
            return ExperimentResult(
                experiment_id=experiment_id,
                status="failed",
                failure_reason="disk_space_exhausted",
                message=pre_check.message,
                completed_runs=0,
                total_planned_runs=len(strategy_specs) * len(test_cases),
            )

        completed_results = []
        total_runs = len(strategy_specs) * len(test_cases)

        for i, (spec, case) in enumerate(itertools.product(strategy_specs, test_cases)):
            # --- Periodic disk check ---
            if disk_monitor.should_check():
                status = disk_monitor.check()
                if status.status == "critical":
                    logger.critical(
                        f"Disk space exhausted at run {i}/{total_runs}. "
                        f"Gracefully aborting experiment '{experiment_id}'."
                    )
                    # Trigger telemetry rotation to reclaim space
                    await self._rotate_telemetry()

                    return ExperimentResult(
                        experiment_id=experiment_id,
                        status="failed",
                        failure_reason="disk_space_exhausted",
                        message=status.message,
                        completed_runs=len(completed_results),
                        total_planned_runs=total_runs,
                        results=completed_results,  # preserve completed work
                    )

            # --- Execute strategy run ---
            result = await self._run_single(spec, case)
            completed_results.append(result)

        return ExperimentResult(
            experiment_id=experiment_id,
            status="completed",
            completed_runs=len(completed_results),
            total_planned_runs=total_runs,
            results=completed_results,
        )

    async def _rotate_telemetry(self):
        """Emergency telemetry rotation to free disk space.

        Removes the oldest raw telemetry files (which are largest).
        Protected/aggregated data is never auto-deleted.
        """
        raw_path = Path(settings.telemetry_storage_path) / "raw"
        if raw_path.exists():
            files = sorted(raw_path.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
            # Remove oldest 50% of raw files
            to_remove = files[:len(files) // 2]
            for f in to_remove:
                f.unlink()
                logger.info(f"Emergency rotation: removed {f.name}")
```

### 7.4 Experiment Result Model

```python
class ExperimentResult(BaseModel):
    """Result of a batch experiment run."""
    experiment_id: str
    status: Literal["completed", "failed", "partial"]
    failure_reason: Optional[str] = None
    message: Optional[str] = None
    completed_runs: int
    total_planned_runs: int
    results: list[dict] = []
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    disk_space_warnings: list[str] = []
```

---

## 8. Summary of Gap Resolutions

| Gap ID | Gap Description | Resolution Section |
|---|---|---|
| **B2-1** | No spec for intermediate state persistence or DAG resumability | §1 — Checkpoint collection, TTL, schema, resume logic |
| **B2-2** | No spec for timeout/cancellation semantics | §2 — Node timeout, strategy timeout, user cancellation state machine |
| **B2-3** | No spec for SSE streaming integration with DAG runner | §3 — Event catalog, legacy adapter, CLI mode, example sequence |
| **B2-4** | No spec for evidence card PII handling during telemetry | §4 — Privacy classes, sanitizer, mode-dependent field handling |
| **EC-1** | Empty profile not handled before strategy execution | §5 — Pre-execution validation, user messages, telemetry skip |
| **EC-2** | Embedding dimension mismatch not detected | §6 — Dimension map, compatibility validator, activation blocking |
| **EC-5** | No disk space protection during overnight exploration | §7 — Monitor, thresholds, graceful abort, emergency rotation |

---

## 9. Implementation Priority

| Priority | Component | Effort | Dependencies |
|---|---|---|---|
| P0 | Empty profile handling (§5) | 1 day | Existing DB access |
| P0 | Embedding dimension check (§6) | 1 day | Profile chunks collection |
| P1 | Timeout semantics (§2) | 2 days | Strategy runner core loop |
| P1 | SSE streaming events (§3) | 2 days | Existing SSE infra in sessions.py |
| P1 | Evidence card PII (§4) | 1 day | Existing pii_pseudonymizer |
| P2 | Checkpoint persistence (§1) | 2 days | MongoDB collection + TTL index |
| P2 | Disk space monitor (§7) | 1 day | Experiment runner |

---

## 10. Acceptance Criteria

1. **Checkpoint:** Long-running strategy (>60s budget) persists state after each node; process restart resumes from checkpoint within 5 seconds.
2. **Timeout:** Node exceeding `timeout_ms` produces `timed_out=true` output; strategy exceeding `latency_hard_limit_ms` halts and returns partial result.
3. **Cancellation:** Client SSE disconnect sets cancelled state; current node finishes within 5s grace; no further nodes dispatched.
4. **SSE Events:** Frontend receives `strategy_started`, per-node events, `synthesis_token` stream, and `strategy_complete`; legacy `phase` events still emitted during migration.
5. **PII:** Evidence cards in telemetry JSONL contain no raw PII when `raw_telemetry_allowed=false`; `source_excerpt` is omitted; `factual_basis` is pseudonymized.
6. **Empty Profile:** Strategy returns localized user message without executing nodes when profile has zero documents.
7. **Dimension Mismatch:** Strategy activation is blocked with actionable error when embedding dimensions differ; runtime warning logged for model drift.
8. **Disk Space:** Experiment aborts gracefully at <100 MB available; completed results are preserved; emergency rotation frees raw telemetry.
