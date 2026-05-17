# Blueprint 13A — Strategy Run State & DAG Execution Semantics

**Addendum to:** Blueprint 02 — Strategy Engine V2: Config-Driven DAG Execution
**Status:** Draft
**Resolves:** Specification gaps B0-1 through B0-6, EC-6
**Target audience:** Implementation engineers building the Strategy Runner
**Integration point:** `backend/agent/strategy_runner.py`, `backend/agent/nodes/*.py`

---

## 1. StrategyRunState Data Contract

The `StrategyRunState` is the single mutable accumulator threaded through every node in a strategy DAG execution. It is created by the `StrategyRunner` at the start of a run and finalized into a `StrategyRunResult` at the end.

### 1.1 Core Pydantic Models

```python
from __future__ import annotations

import time
import uuid
from copy import deepcopy
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """A single chunk returned by a retrieval node."""
    chunk_id: str
    document_id: str
    document_title: str
    document_version: Optional[str] = None
    source_id: str
    content: str
    score: float
    search_type: Literal["semantic", "text", "hybrid"] = "hybrid"
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceCard(BaseModel):
    """Compressed evidence unit produced by the evidence_cards node."""
    card_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    topic: str
    source_id: str
    document_title: str
    document_version: Optional[str] = None
    span_id: Optional[str] = None
    source_excerpt: str
    factual_basis: str
    confidence: float
    card_type: str = "fact_card"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SynthesisResult(BaseModel):
    """Output of a synthesize node."""
    text: str
    citations: list[CitationRef] = Field(default_factory=list)
    language: str = "en"
    format_id: Optional[str] = None
    token_count: int = 0
    model_used: str = ""
    confidence: Optional[float] = None


class CitationRef(BaseModel):
    """A single citation reference within a synthesis."""
    citation_key: str          # e.g. "[1]"
    document_id: str
    document_title: str
    span_id: Optional[str] = None
    chunk_id: Optional[str] = None
    excerpt: str = ""


class ValidationResult(BaseModel):
    """Output of a validation node (contract, citation, quality)."""
    validator_id: str          # node id that produced this
    passed: bool
    score: Optional[float] = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    """A single issue found by a validator."""
    severity: Literal["error", "warning", "info"]
    code: str                  # machine-readable, e.g. "missing_citation"
    message: str
    location: Optional[str] = None  # JSONPath or text span


class BusinessContextResult(BaseModel):
    """Output of the business_context node."""
    capability_id: Optional[str] = None
    profile_key: Optional[str] = None
    source_ids: list[str] = Field(default_factory=list)
    answer_format_id: Optional[str] = None
    language: str = "en"
    tone: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NodeOutput(BaseModel):
    """Wrapper for the output of any single node execution."""
    node_id: str
    node_type: str
    status: Literal["success", "skipped", "empty", "error"] = "success"
    started_at_ms: float
    finished_at_ms: float
    duration_ms: float = 0.0
    output_data: Any = None       # typed per node_type; see §1.3
    error_message: Optional[str] = None
    retry_count: int = 0
    model_used: Optional[str] = None
    tokens_used: int = 0


class StrategyRunState(BaseModel):
    """Accumulator for an entire strategy DAG execution.

    Created once per run. Nodes read from state via typed accessors
    and produce NodeOutput objects. The runner merges each NodeOutput
    into the state after the node completes.
    """
    # ── Identity ──
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    # ── Input ──
    query: str
    normalized_query: Optional[str] = None

    # ── Accumulated outputs (append-only) ──
    business_context: Optional[BusinessContextResult] = None
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    synthesis_result: Optional[SynthesisResult] = None
    validation_results: list[ValidationResult] = Field(default_factory=list)

    # ── Node-level outputs ──
    node_outputs: dict[str, NodeOutput] = Field(default_factory=dict)

    # ── Run metadata ──
    metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: float = 0.0
    cancelled: bool = False

    # ── Internal bookkeeping (not serialized to telemetry) ──
    _start_time: float = 0.0

    class Config:
        arbitrary_types_allowed = True

    # ── Typed accessor methods ──

    def get_chunks(self) -> list[RetrievedChunk]:
        """Return a deep copy of retrieved chunks (parallel-safe)."""
        return deepcopy(self.retrieved_chunks)

    def get_evidence(self) -> list[EvidenceCard]:
        """Return a deep copy of evidence cards (parallel-safe)."""
        return deepcopy(self.evidence_cards)

    def get_node_output(self, node_id: str) -> Optional[NodeOutput]:
        """Return a deep copy of a specific node's output."""
        out = self.node_outputs.get(node_id)
        return deepcopy(out) if out else None

    def get_synthesis(self) -> Optional[SynthesisResult]:
        """Return a deep copy of the synthesis result."""
        return deepcopy(self.synthesis_result) if self.synthesis_result else None

    def has_node_completed(self, node_id: str) -> bool:
        return node_id in self.node_outputs

    def is_node_successful(self, node_id: str) -> bool:
        out = self.node_outputs.get(node_id)
        return out is not None and out.status == "success"
```

### 1.2 Access Semantics

| Rule | Description |
|------|-------------|
| **Read via accessor** | Nodes MUST read from state using typed accessor methods (`get_chunks()`, `get_evidence()`, etc.). Accessors return deep copies so parallel nodes cannot observe partial writes from siblings. |
| **Produce NodeOutput** | Every node returns a `NodeOutput` (or raises). Nodes never write to `StrategyRunState` directly. |
| **Runner merges** | After a node completes, the runner calls `_merge_node_output()` which: (1) stores the `NodeOutput` in `node_outputs[node_id]`, (2) appends typed data to the appropriate accumulator list (e.g. `retrieved_chunks`, `evidence_cards`). |
| **Append-only** | Nodes cannot mutate previous node outputs. They can only append new data. The runner enforces this by raising `StateViolationError` if a node attempts to overwrite an existing `node_outputs` key. |
| **Copy-on-access** | All accessor methods return `deepcopy`. This is the sole mechanism for parallel safety — no locks are needed for reads. |

### 1.3 NodeOutput.output_data Type Mapping

Each `node_type` produces a specific type in `NodeOutput.output_data`:

| Node type | output_data type |
|-----------|-----------------|
| `normalize_query` | `str` (the normalized query) |
| `business_context` | `BusinessContextResult` |
| `intent_classify` | `dict[str, Any]` with keys `intent`, `confidence`, `domain` |
| `retrieve` | `list[RetrievedChunk]` |
| `query_expand` | `list[str]` (expanded queries) |
| `rerank` | `list[RetrievedChunk]` (re-scored) |
| `dedupe_versions` | `list[RetrievedChunk]` (deduplicated) |
| `boilerplate_filter` | `list[RetrievedChunk]` (filtered) |
| `evidence_cards` | `list[EvidenceCard]` |
| `plan` | `dict[str, Any]` with keys `plan_text`, `steps` |
| `synthesize` | `SynthesisResult` |
| `validate_citations` | `ValidationResult` |
| `validate_contract` | `ValidationResult` |
| `judge_quality` | `ValidationResult` |
| `refine` | `SynthesisResult` (revised) |
| `compare_candidates` | `SynthesisResult` (chosen best) |
| `emit_telemetry` | `None` (side-effect only) |
| `legacy_orchestrator_pipeline` | `dict` with keys `synthesis`, `chunks`, `phase_metrics` |

### 1.4 State Merge Rules

```python
def _merge_node_output(self, state: StrategyRunState, output: NodeOutput) -> None:
    """Merge a completed node's output into the run state.

    Raises StateViolationError if node_id already exists in node_outputs.
    """
    if output.node_id in state.node_outputs:
        raise StateViolationError(
            f"Node '{output.node_id}' already has output in state. "
            "Nodes cannot overwrite previous outputs."
        )
    state.node_outputs[output.node_id] = output

    if output.status != "success" or output.output_data is None:
        return

    match output.node_type:
        case "normalize_query":
            state.normalized_query = output.output_data
        case "business_context":
            state.business_context = output.output_data
        case "retrieve" | "rerank" | "dedupe_versions" | "boilerplate_filter":
            state.retrieved_chunks.extend(output.output_data)
        case "evidence_cards":
            state.evidence_cards.extend(output.output_data)
        case "synthesize" | "refine" | "compare_candidates":
            state.synthesis_result = output.output_data
        case "validate_citations" | "validate_contract" | "judge_quality":
            state.validation_results.append(output.output_data)
        case "legacy_orchestrator_pipeline":
            data = output.output_data
            if "synthesis" in data:
                state.synthesis_result = data["synthesis"]
            if "chunks" in data:
                state.retrieved_chunks.extend(data["chunks"])
```

---

## 2. Node Execution Order for Conditional DAGs

### 2.1 Condition Expressions

Edges in `StrategyGraph` may carry a `condition: Optional[str]` field. Conditions are string expressions evaluated against the current `StrategyRunState`. They use a restricted evaluator that permits only safe, side-effect-free operations.

**Allowed operations (whitelist):**

| Category | Allowed |
|----------|---------|
| Built-in functions | `len`, `any`, `all`, `bool`, `int`, `float`, `str`, `min`, `max`, `abs` |
| Comparisons | `==`, `!=`, `<`, `<=`, `>`, `>=` |
| Boolean logic | `and`, `or`, `not` |
| Membership | `in`, `not in` |
| Attribute access | Dot notation on state fields only |
| Indexing | `[]` on lists and dicts |
| Literals | Strings, integers, floats, booleans, `None` |

**Blocked:** imports, function calls other than whitelisted, assignments, comprehensions, walrus operator, `exec`, `eval`, `__dunder__` access.

**Implementation:** Use `ast.parse(expr, mode='eval')` and walk the AST, rejecting any node type not in the whitelist before calling `compile()` + `eval()` with a restricted namespace containing only `state` and the whitelisted builtins.

**Examples:**

```python
# Edge from 'retrieve' to 'evidence_cards' — only if chunks were found
"len(state.retrieved_chunks) > 0"

# Edge from 'validate' to 'refine' — only if validation failed
"any(not v.passed for v in state.validation_results)"

# Edge from 'context' to 'retrieve_web' — only if web sources allowed
"state.business_context is not None and 'web' in state.business_context.source_ids"

# Unconditional edge (condition is None or omitted)
None
```

### 2.2 Node Reachability Rules

| Scenario | Behavior |
|----------|----------|
| Edge has `condition: None` | Unconditional — target node is always reachable via this edge |
| Edge has condition that evaluates to `True` | Target node is reachable via this edge |
| Edge has condition that evaluates to `False` | Edge is not traversed |
| Node has **at least one unconditional** incoming edge | Node always executes (regardless of conditional edges) |
| Node has **only conditional** incoming edges, **all** false | Node is **skipped**; its `NodeOutput` is recorded with `status="skipped"` |
| Node has **only conditional** incoming edges, **at least one** true | Node executes normally |
| Node has **no incoming edges** (entry node) | Always executes |

**Transitive skip:** If node A is skipped, all edges from A are treated as non-existent for reachability of downstream nodes.

### 2.3 Execution Algorithm

The runner uses **Kahn's algorithm** for topological ordering, extended with condition evaluation at each level boundary.

```
ALGORITHM: ConditionalDAGExecute(graph, state)
────────────────────────────────────────────────
INPUT:  graph = (nodes, edges), state = StrategyRunState
OUTPUT: state (mutated with all node outputs)

1.  Compute in_degree[n] for each node n
2.  Initialize queue Q with all nodes where in_degree[n] == 0
3.  Initialize reachable = { n : True for n in Q }       # entry nodes are reachable
4.  Initialize skipped = set()

5.  WHILE Q is not empty:
6.      level_nodes = drain(Q)                            # all currently ready nodes

7.      FOR each node N in level_nodes:
8.          IF N not in reachable OR reachable[N] == False:
9.              Record NodeOutput(node_id=N, status="skipped")
10.             skipped.add(N)
11.             FOR each outgoing edge (N → T):
12.                 in_degree[T] -= 1
13.                 IF in_degree[T] == 0: add T to Q
14.             CONTINUE

15.         result = await execute_node(N, state)         # see §7 for parallel variant
16.         merge_node_output(state, result)
17.
18.         IF result.halt:                                # see §3 for halt semantics
19.             CANCEL all remaining nodes
20.             RETURN state
21.
22.         FOR each outgoing edge (N → T) with condition C:
23.             IF C is None OR restricted_eval(C, state) == True:
24.                 mark edge as "satisfied"
25.             in_degree[T] -= 1
26.             IF in_degree[T] == 0:
27.                 # Determine reachability of T
28.                 incoming_edges_to_T = all edges (X → T)
29.                 has_unconditional = any(e.condition is None AND X not in skipped
30.                                        for e in incoming_edges_to_T)
31.                 has_satisfied_cond = any(e is marked "satisfied"
32.                                         for e in incoming_edges_to_T)
33.                 reachable[T] = has_unconditional OR has_satisfied_cond
34.                 add T to Q

35. RETURN state
```

### 2.4 Condition Evaluation Failure

If a condition string raises an exception during `restricted_eval`:

- Log `WARNING` with the expression and exception.
- Treat the condition as `False` (edge not traversed).
- Record the failure in `state.metadata["condition_eval_errors"]`.

This is a **fail-closed** policy: broken conditions never accidentally enable nodes.

---

## 3. Error Handling for Empty/Failed Node Results

### 3.1 Node-Level Declarations

The `StrategyNode` model (from Blueprint 02 §4) is extended with error-handling directives:

```python
class StrategyNode(BaseModel):
    id: str
    type: str
    enabled: bool = True
    config: dict = {}
    timeout_ms: Optional[int] = None
    model_role: Optional[str] = None
    prompt_id: Optional[str] = None

    # ── Error handling (new fields) ──
    on_empty: Literal["skip", "error", "fallback"] = "skip"
    on_empty_fallback_node: Optional[str] = None   # required when on_empty="fallback"
    on_error: Literal["skip", "halt", "retry"] = "halt"
    retry_config: Optional[RetryConfig] = None      # required when on_error="retry"


class RetryConfig(BaseModel):
    max_retries: int = 2
    backoff_ms: int = 500
    backoff_multiplier: float = 2.0
    retry_on: list[str] = Field(
        default_factory=lambda: ["TimeoutError", "ConnectionError", "LLMProviderError"]
    )
```

### 3.2 Empty Result Semantics

A node result is considered "empty" when:

- `NodeOutput.output_data` is `None`, empty list `[]`, or empty dict `{}`
- `NodeOutput.status == "empty"` (node explicitly signals empty)

**on_empty behavior:**

| `on_empty` value | Runner behavior |
|------------------|-----------------|
| `"skip"` | Record `NodeOutput(status="empty")`. Downstream nodes receive empty data and must handle it. Terminal synthesis nodes produce a "no relevant information found" response. |
| `"error"` | Halt the entire strategy run. Return `StrategyRunResult(success=False, halt_reason="empty_result", halt_node=node_id)`. |
| `"fallback"` | Execute the node identified by `on_empty_fallback_node` instead. If the fallback also returns empty, apply the fallback node's own `on_empty` policy. Circular fallback references are rejected at spec validation time. |

### 3.3 Error (Exception) Semantics

**on_error behavior:**

| `on_error` value | Runner behavior |
|------------------|-----------------|
| `"skip"` | Record `NodeOutput(status="error", error_message=str(e))`. Downstream nodes see this node as not having produced data. Log `WARNING`. |
| `"halt"` | Stop the entire strategy run immediately. Return `StrategyRunResult(success=False, halt_reason="node_error", halt_node=node_id, error=str(e))`. |
| `"retry"` | Retry with exponential backoff per `retry_config`. On each retry: `delay = backoff_ms * (backoff_multiplier ^ attempt)`. If all retries exhausted, escalate to `on_error="halt"` behavior. |

### 3.4 Decision Tree

```
Node completes execution
│
├── Exception raised?
│   ├── YES
│   │   ├── on_error = "skip"
│   │   │   └── Record error output → continue DAG
│   │   ├── on_error = "halt"
│   │   │   └── HALT strategy → return failure result
│   │   └── on_error = "retry"
│   │       ├── retries remaining?
│   │       │   ├── YES → wait backoff_ms → retry node
│   │       │   └── NO  → HALT strategy → return failure result
│   │
│   └── NO (node returned normally)
│       ├── Result is empty?
│       │   ├── YES
│       │   │   ├── on_empty = "skip"
│       │   │   │   └── Record empty output → continue DAG
│       │   │   │       └── If terminal synthesize sees no data:
│       │   │   │           → produce "no relevant information found"
│       │   │   ├── on_empty = "error"
│       │   │   │   └── HALT strategy → return failure result
│       │   │   └── on_empty = "fallback"
│       │   │       └── Execute fallback node → apply its policy
│       │   │
│       │   └── NO (result has data)
│       │       └── Merge output into state → continue DAG
```

### 3.5 Downstream Behavior Examples

**Scenario A:** Retrieve returns 0 results, `on_empty="skip"`

```
normalize → context → retrieve(on_empty=skip) → evidence_cards → synthesize → emit
                           │ returns []
                           ▼
                   state.retrieved_chunks = []
                   evidence_cards: receives empty input → produces []
                   synthesize: sees no evidence → generates:
                     "No relevant information was found for your query.
                      Try broadening your search terms or checking
                      that the relevant documents have been ingested."
```

**Scenario B:** Retrieve returns 0 results, `on_empty="error"`

```
normalize → context → retrieve(on_empty=error) → HALT
                           │ returns []
                           ▼
                   StrategyRunResult(
                       success=False,
                       halt_reason="empty_result",
                       halt_node="retrieve",
                   )
```

**Scenario C:** Node throws, `on_error="retry"` (max_retries=2)

```
normalize → context → retrieve(on_error=retry, max_retries=2) → ...
                           │ attempt 1: TimeoutError
                           │ wait 500ms
                           │ attempt 2: TimeoutError
                           │ wait 1000ms
                           │ attempt 3: TimeoutError
                           ▼
                   All retries exhausted → HALT
                   StrategyRunResult(
                       success=False,
                       halt_reason="node_error",
                       halt_node="retrieve",
                       error="TimeoutError after 3 attempts",
                   )
```

---

## 4. Model Role Fallback When Role Unavailable

### 4.1 ModelRoleConfig

```python
class ModelRoleConfig(BaseModel):
    """Configuration for a single model role within a strategy."""
    role_id: str                                    # e.g. "orchestrator", "worker_fast"
    provider: str                                   # "ollama", "openai", "anthropic", "google"
    model: str                                      # e.g. "llama3.2", "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 4096
    fallback_role: Optional[str] = None             # role_id to fall back to
    cloud_only: bool = False                        # if True, blocked in local_only mode
    tags: list[str] = Field(default_factory=list)   # e.g. ["fast", "reasoning"]
```

### 4.2 Registration-Time Validation (Fail-Fast)

When a `StrategySpec` is registered with the `StrategyRegistry`, the following validations run synchronously:

1. **Role reference check:** Every `model_role` reference in `graph.nodes[*].model_role` and `prompts[*]` must exist as a key in `spec.model_roles`.
2. **Fallback chain acyclicity:** Follow `fallback_role` chains; reject if a cycle is detected.
3. **Local-only constraint:** If `spec.budgets.local_only == True`, reject any role where `cloud_only == True` unless a non-cloud fallback exists.
4. **Worker role existence:** `spec.model_roles` must contain a `"worker"` role (universal last-resort).

Validation failures raise `StrategySpecValidationError` with a list of all issues found (not just the first).

### 4.3 Runtime Fallback Algorithm

```
FUNCTION resolve_model_for_role(role_id, spec, runtime_context) → (provider, model)
─────────────────────────────────────────────────────────────────────────────────────
1.  role_config = spec.model_roles[role_id]
2.  IF runtime_context.local_only AND role_config.cloud_only:
        GOTO step 4                          # skip directly to fallback
3.  IF model_is_available(role_config.provider, role_config.model):
        RETURN (role_config.provider, role_config.model)
4.  # Primary model unavailable — try fallback chain
5.  IF role_config.fallback_role is not None:
        log WARNING: f"Role '{role_id}' model unavailable, trying fallback '{role_config.fallback_role}'"
        emit_telemetry("model_fallback", {role_id, fallback=role_config.fallback_role})
        RETURN resolve_model_for_role(role_config.fallback_role, spec, runtime_context)
6.  # No explicit fallback — use universal 'worker' role
7.  IF role_id != "worker":
        log ERROR: f"Role '{role_id}' has no fallback, using 'worker' as last resort"
        emit_telemetry("model_fallback_worker", {role_id})
        RETURN resolve_model_for_role("worker", spec, runtime_context)
8.  # Even 'worker' role is unavailable
9.  RAISE ModelUnavailableError(
        f"No available model for role '{role_id}' and universal 'worker' fallback is also unavailable. "
        "Strategy cannot execute."
    )
```

### 4.4 Availability Check

`model_is_available(provider, model)` performs a lightweight check:

| Provider | Check method |
|----------|-------------|
| `ollama` | `GET /api/tags` — verify model name is in the returned list |
| `openai` | Assume available if API key is set (no list endpoint) |
| `anthropic` | Assume available if API key is set |
| `google` | Assume available if API key is set |

Cache availability results for 60 seconds to avoid per-node HTTP round-trips. Invalidate on any `ConnectionError`.

### 4.5 Fallback Example

```yaml
model_roles:
  orchestrator:
    role_id: orchestrator
    provider: openai
    model: gpt-4o
    temperature: 0.3
    max_tokens: 4096
    fallback_role: orchestrator_compact
    cloud_only: true

  orchestrator_compact:
    role_id: orchestrator_compact
    provider: ollama
    model: llama3.2
    temperature: 0.3
    max_tokens: 4096
    fallback_role: worker

  worker:
    role_id: worker
    provider: ollama
    model: llama3.2
    temperature: 0.7
    max_tokens: 2048

  worker_fast:
    role_id: worker_fast
    provider: ollama
    model: phi3
    temperature: 0.5
    max_tokens: 1024
    fallback_role: worker
```

In `local_only` mode: `orchestrator` (cloud_only) → skips to `orchestrator_compact` (ollama) → available → uses it. No error.

---

## 5. Legacy Adapter Node Configuration

### 5.1 Purpose

The `legacy_orchestrator_pipeline` node type wraps the existing `FederatedAgent` (see `backend/agent/coordinator.py`) as a single strategy node. This enables:

- Side-by-side comparison of DAG strategies vs. legacy orchestrator.
- Incremental migration without deleting working code.
- A single-node strategy spec for teams not yet ready for full DAG decomposition.

### 5.2 Node Config Schema

```python
class LegacyOrchestratorConfig(BaseModel):
    """Config schema for the legacy_orchestrator_pipeline node type."""
    max_iterations: int = 3
    parallel_workers: int = 4
    timeout_ms: int = 120_000
    early_exit: bool = True
    mode: Literal["thinking", "fast", "auto"] = "auto"
    confidence_threshold: float = 0.8
```

**Mapping to `FederatedAgent` constructor:**

| Config field | FederatedAgent / AgentModeConfig field |
|-------------|---------------------------------------|
| `max_iterations` | `AgentModeConfig.max_iterations` |
| `parallel_workers` | `AgentModeConfig.parallel_workers` |
| `timeout_ms` | Used to set `asyncio.wait_for` around `process_message()` |
| `early_exit` | `StrategyConfig.early_exit_enabled` |
| `mode` | `AgentModeConfig.mode` (maps to `AgentMode` enum) |
| `confidence_threshold` | `StrategyConfig.confidence_threshold` |

### 5.3 Output Mapping

The legacy adapter node produces `NodeOutput.output_data` as a dict:

```python
{
    "synthesis": SynthesisResult(
        text=agent_response.response,
        citations=[...],          # extracted from agent trace
        language=detected_lang,
        model_used=trace.model_used,
        token_count=trace.total_tokens,
    ),
    "chunks": [
        RetrievedChunk(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            document_title=hit.document_title,
            source_id=hit.source_id,
            content=hit.content,
            score=hit.score,
            search_type=hit.search_type,
        )
        for hit in trace.all_search_hits
    ],
    "phase_metrics": {
        phase.phase: {
            "duration_ms": phase.duration_ms,
            "tokens_used": phase.tokens_used,
            "success": phase.success,
        }
        for phase in trace.phase_metrics
    },
}
```

### 5.4 Example StrategySpec YAML — Legacy Adapter as Single-Node DAG

```yaml
id: legacy_full_orchestrator
version: 1.0.0
display_name: Legacy Full Orchestrator (Wrapped)
description: >
  Wraps the existing FederatedAgent as a single strategy node.
  Used as baseline for A/B comparison against new DAG strategies.
status: active
tenant_scope: [recallhub, quellex]
capability_ids: ["*"]
domains: [general]
tags: [legacy, baseline, full_orchestrator]

selection:
  auto_select_when:
    capability_id: "*"
    agent_mode: [auto, thinking]
  priority: 10                    # low priority — only selected as fallback

budgets:
  latency_target_ms: 30000
  latency_hard_limit_ms: 120000
  context_budget_tokens: 8000
  output_budget_tokens: 2000
  max_llm_calls: 12
  max_search_operations: 8
  local_only: false

source_policy:
  primary_sources: [active_profile]
  allow_cross_matter: false
  allow_web: true
  require_source_spans: false
  prefer_latest_versions: true
  exclude_boilerplate: false

retrieval_policy:
  search_type: hybrid
  top_k: 10
  query_variants: 2
  use_query_expansion: true

evidence_policy:
  enabled: false                 # legacy adapter handles its own evidence

answer_contract:
  format_id: general_response
  language: auto
  tone: professional
  citation_granularity: document
  unsupported_claim_policy: label

validation_policy:
  require_answer_contract: false  # legacy has its own evaluation loop

model_roles:
  orchestrator:
    role_id: orchestrator
    provider: ollama
    model: llama3.2
    temperature: 0.3
    max_tokens: 4096
  worker:
    role_id: worker
    provider: ollama
    model: llama3.2
    temperature: 0.7
    max_tokens: 2048

graph:
  entry_node: legacy
  terminal_nodes: [emit]
  nodes:
    - id: legacy
      type: legacy_orchestrator_pipeline
      config:
        max_iterations: 3
        parallel_workers: 4
        timeout_ms: 120000
        early_exit: true
        mode: auto
      on_empty: skip
      on_error: halt
    - id: emit
      type: emit_telemetry
  edges:
    - from_node: legacy
      to_node: emit
```

---

## 6. Telemetry Schema Integration

### 6.1 Dual-Schema Strategy

The system must support two telemetry schemas simultaneously during the migration from legacy orchestrator to DAG runner:

| Schema version | Populated by | `phase_metrics` | `strategy_node_metrics` |
|---------------|-------------|-----------------|------------------------|
| `1` (legacy) | Legacy orchestrator path | Populated | `null` or empty |
| `2` (strategy-os) | New DAG runner | `null` or empty | Populated |
| `1+2` (bridge) | Legacy adapter node in DAG | Populated (backward compat) | Populated (phases mapped as nodes) |

### 6.2 TelemetryRecord Extension

```python
class StrategyNodeMetric(BaseModel):
    """Telemetry for a single strategy node execution."""
    node_id: str
    node_type: str
    status: Literal["success", "skipped", "empty", "error"]
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    model_used: Optional[str] = None
    model_role: Optional[str] = None
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retry_count: int = 0
    error_message: Optional[str] = None
    input_summary: str = ""       # truncated/pseudonymized input description
    output_summary: str = ""      # truncated/pseudonymized output description


class TelemetryRecordV2(TelemetryRecord):
    """Extended telemetry record with strategy DAG metrics.

    Inherits all fields from TelemetryRecord and adds strategy-specific
    telemetry. Analytics endpoints normalize both v1 and v2 records
    via an internal adapter.
    """
    # ── Strategy identification ──
    strategy_id: str = ""
    strategy_version: str = ""
    strategy_spec_hash: str = ""   # see §8

    # ── Schema versioning ──
    telemetry_schema_version: int = 1
    # 1 = legacy (phase_metrics only)
    # 2 = strategy-os (strategy_node_metrics only)

    # ── DAG execution metrics ──
    strategy_node_metrics: list[StrategyNodeMetric] = Field(default_factory=list)

    # ── DAG execution summary ──
    dag_total_nodes: int = 0
    dag_executed_nodes: int = 0
    dag_skipped_nodes: int = 0
    dag_failed_nodes: int = 0
    dag_parallel_levels: int = 0
    dag_max_level_width: int = 0   # max nodes executing in parallel at any level
```

### 6.3 Legacy Adapter Phase-to-Node Mapping

When the `legacy_orchestrator_pipeline` node runs, it maps legacy phases to `StrategyNodeMetric` entries for unified analytics:

| Legacy phase | Mapped node_id | Mapped node_type |
|-------------|---------------|-----------------|
| `analyze` | `legacy.analyze` | `intent_classify` |
| `plan` | `legacy.plan` | `plan` |
| `execute` (worker pool) | `legacy.execute` | `retrieve` |
| `evaluate` | `legacy.evaluate` | `judge_quality` |
| `synthesize` | `legacy.synthesize` | `synthesize` |
| `refine` (if triggered) | `legacy.refine` | `refine` |

This mapping ensures analytics dashboards that query `strategy_node_metrics` can include legacy runs without schema branching.

### 6.4 Analytics Normalization

Analytics endpoints (e.g., latency percentiles, token usage breakdowns) use an internal adapter:

```python
def normalize_telemetry(record: TelemetryRecordV2) -> NormalizedMetrics:
    """Normalize v1 and v2 telemetry into a common analytics shape."""
    if record.telemetry_schema_version == 1:
        # Convert phase_metrics → node-shaped metrics
        return NormalizedMetrics(
            nodes=[
                NormalizedNode(
                    node_id=f"legacy.{pm.phase}",
                    duration_ms=pm.duration_ms,
                    tokens_used=pm.tokens_used,
                    success=pm.success,
                )
                for pm in record.phase_metrics
            ],
            total_duration_ms=record.total_duration_ms,
        )
    else:
        # Use strategy_node_metrics directly
        return NormalizedMetrics(
            nodes=[
                NormalizedNode(
                    node_id=snm.node_id,
                    duration_ms=snm.duration_ms,
                    tokens_used=snm.tokens_used,
                    success=snm.status == "success",
                )
                for snm in record.strategy_node_metrics
            ],
            total_duration_ms=record.total_duration_ms,
        )
```

---

## 7. DAG Parallel Node Execution Model

### 7.1 Execution Model: Level-Synchronous

All nodes at the same topological level execute in parallel using `asyncio.gather()`. The runner waits for **all** nodes in a level to complete before advancing to the next level.

**Why level-synchronous (not fully async)?**

- **Predictable state:** All nodes at level N can read state produced by levels 0..N-1 without races.
- **Simpler error handling:** Halt/cancel decisions apply cleanly at level boundaries.
- **Easier debugging:** Telemetry shows clear level-by-level execution trace.
- **Sufficient parallelism:** Most strategy DAGs have 2-4 parallel branches; full async adds complexity with minimal latency benefit.

### 7.2 State Safety Model

| Concern | Mechanism |
|---------|-----------|
| Parallel reads | Safe — accessor methods return `deepcopy`. No locks needed. |
| Parallel writes | Safe — each node writes to its own unique key in `node_outputs[node_id]`. No two parallel nodes share a key. |
| Accumulator lists | Writes to `retrieved_chunks`, `evidence_cards`, etc. happen in `_merge_node_output()` which runs **sequentially after gather returns**, not during parallel execution. |
| Cancellation | Uses `asyncio.Task.cancel()` — nodes must be cancellation-safe (use `try/finally` for cleanup). |

**Key invariant:** During parallel execution within a level, no node can observe another node's output from the same level. Nodes can only read state from completed previous levels.

### 7.3 Error Handling in Parallel Execution

| Scenario | Behavior |
|----------|----------|
| Node fails with `on_error="halt"` | Cancel all other tasks at the same level via `asyncio.Task.cancel()`. Wait for cancellations to propagate. Halt strategy. |
| Node fails with `on_error="skip"` | Let all other tasks at the same level finish. Record failed node as `status="error"`. Continue to next level. |
| Node fails with `on_error="retry"` | Retry happens inside the node's task (does not block other parallel nodes). If all retries fail, escalate per retry exhaustion policy (halt). |
| Multiple nodes fail with `halt` in same level | First halt wins. All nodes are cancelled. |

### 7.4 Resource Limits

The `StrategyBudgets` model is extended with:

```python
class StrategyBudgets(BaseModel):
    # ... existing fields from Blueprint 02 §5 ...
    latency_target_ms: int
    latency_hard_limit_ms: int
    context_budget_tokens: int
    output_budget_tokens: int
    max_llm_calls: int
    max_search_operations: int
    max_cost_eur: Optional[float] = None
    local_only: bool = False
    max_gpu_memory_gb: Optional[float] = None
    max_cpu_utilization_pct: Optional[int] = None

    # ── Parallel execution limits (new) ──
    max_parallel_nodes: int = 4
```

If a topological level contains more nodes than `max_parallel_nodes`, the runner batches them:

```python
async def _execute_level(self, nodes: list[StrategyNode], state: StrategyRunState) -> list[NodeOutput]:
    results = []
    batch_size = self.spec.budgets.max_parallel_nodes

    for i in range(0, len(nodes), batch_size):
        batch = nodes[i : i + batch_size]
        batch_results = await asyncio.gather(
            *(self._execute_single_node(node, state) for node in batch),
            return_exceptions=True,
        )
        # Process results, handle errors/halts
        for node, result in zip(batch, batch_results):
            output = self._handle_gather_result(node, result)
            results.append(output)
            if output.status == "error" and node.on_error == "halt":
                # Cancel remaining batches
                return results
        # Merge batch outputs into state before next batch
        for output in results[len(results) - len(batch):]:
            self._merge_node_output(state, output)

    return results
```

### 7.5 Full Algorithm Pseudo-Code

```
ALGORITHM: LevelSynchronousDAGExecute(spec, initial_state)
──────────────────────────────────────────────────────────────
INPUT:  spec = StrategySpec, initial_state = StrategyRunState
OUTPUT: StrategyRunResult

1.  state = initial_state
2.  state._start_time = time.monotonic()
3.  graph = compile_graph(spec.graph)        # validate, build adjacency
4.  levels = topological_levels(graph)       # list[list[node_id]]
5.  reachable = compute_initial_reachability(graph)
6.  halted = False

7.  FOR level_idx, level_node_ids IN enumerate(levels):
8.      # Filter to reachable, enabled nodes
9.      active_nodes = [
10.         graph.nodes[nid] for nid in level_node_ids
11.         if reachable[nid] and graph.nodes[nid].enabled
12.     ]
13.     skipped_nodes = [
14.         nid for nid in level_node_ids
15.         if not reachable[nid] or not graph.nodes[nid].enabled
16.     ]
17.
18.     # Record skipped
19.     FOR nid IN skipped_nodes:
20.         state.node_outputs[nid] = NodeOutput(
21.             node_id=nid, node_type=graph.nodes[nid].type,
22.             status="skipped", started_at_ms=now(), finished_at_ms=now()
23.         )
24.
25.     IF len(active_nodes) == 0:
26.         CONTINUE
27.
28.     # Execute active nodes in parallel (batched by max_parallel_nodes)
29.     level_outputs = await execute_level(active_nodes, state)  # see §7.4
30.
31.     # Check for halts
32.     FOR output IN level_outputs:
33.         IF output triggers halt:
34.             halted = True
35.             BREAK
36.
37.     IF halted:
38.         BREAK
39.
40.     # Update reachability for next level
41.     FOR output IN level_outputs:
42.         FOR edge IN graph.outgoing_edges(output.node_id):
43.             evaluate condition against updated state
44.             update reachable[edge.to_node] accordingly
45.
46.     # Budget check
47.     state.elapsed_ms = (time.monotonic() - state._start_time) * 1000
48.     IF state.elapsed_ms > spec.budgets.latency_hard_limit_ms:
49.         halted = True
50.         halt_reason = "latency_budget_exceeded"
51.         BREAK
52.
53. state.elapsed_ms = (time.monotonic() - state._start_time) * 1000
54. RETURN build_strategy_run_result(state, halted, halt_reason)
```

### 7.6 Topological Level Computation

```python
def topological_levels(graph: StrategyGraph) -> list[list[str]]:
    """Compute topological levels using Kahn's algorithm.

    Returns a list of levels, where each level is a list of node IDs
    that can execute in parallel.
    """
    in_degree = {n.id: 0 for n in graph.nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in graph.nodes}

    for edge in graph.edges:
        in_degree[edge.to_node] += 1
        adjacency[edge.from_node].append(edge.to_node)

    levels: list[list[str]] = []
    queue = [nid for nid, deg in in_degree.items() if deg == 0]

    while queue:
        levels.append(queue)
        next_queue = []
        for nid in queue:
            for neighbor in adjacency[nid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_queue.append(neighbor)
        queue = next_queue

    total_scheduled = sum(len(level) for level in levels)
    if total_scheduled != len(graph.nodes):
        raise StrategyGraphCycleError(
            f"Graph has a cycle: scheduled {total_scheduled} of {len(graph.nodes)} nodes"
        )

    return levels
```

---

## 8. Spec Hash Computation

### 8.1 Algorithm

The spec hash uniquely identifies a `StrategySpec` configuration for deduplication, telemetry correlation, and evaluation linking.

```python
import hashlib
import json

def compute_spec_hash(spec: StrategySpec) -> str:
    """Compute a stable SHA-256 hash of a StrategySpec.

    The hash is computed over a canonical JSON serialization:
    - Keys are sorted recursively.
    - No whitespace (separators=(',', ':')).
    - Fields excluded from hashing: 'status', 'experiment'.
    - Enums serialized as their string values.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    # Serialize to dict, excluding volatile/non-semantic fields
    spec_dict = spec.model_dump(
        exclude={"status", "experiment"},
        mode="json",
    )
    canonical_json = json.dumps(
        spec_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
```

### 8.2 Usage

| Use case | How spec_hash is used |
|----------|----------------------|
| **Dedup detection** | Before registering a spec variant, check if a spec with the same hash already exists. If so, reject as duplicate or link to existing. |
| **Telemetry correlation** | Every `TelemetryRecordV2` includes `strategy_spec_hash`. Analytics can group performance metrics by exact spec version. |
| **Evaluation linking** | Evaluation results (quality scores, human feedback) are linked to `spec_hash` so that score regressions can be detected across spec changes. |
| **Version history** | When a spec is updated, the old hash is retained in `spec.experiment.previous_spec_hashes: list[str]`. |

### 8.3 Exclusions

The following fields are **excluded** from hash computation because they do not affect execution behavior:

| Excluded field | Reason |
|---------------|--------|
| `status` | Lifecycle state (draft → active → deprecated) changes without affecting the spec's execution semantics. |
| `experiment` | Experiment metadata (A/B group, previous hashes, notes) is administrative, not behavioral. |

### 8.4 Hash Stability Contract

- **Recompute on change:** Any modification to a hashed field produces a new hash.
- **Cross-platform stability:** Using `ensure_ascii=True` and `sort_keys=True` guarantees identical hashes across Python versions and operating systems.
- **Model version pinning:** The hash includes `model_roles` configurations. Changing a model name (e.g., `llama3.2` → `llama3.3`) produces a different hash — this is intentional, as model behavior differs.

---

## Appendix A: StrategyRunResult

The final output of a strategy run, returned to the coordinator:

```python
class StrategyRunResult(BaseModel):
    """Final result of a strategy DAG execution."""
    run_id: str
    trace_id: str
    strategy_id: str
    strategy_version: str
    strategy_spec_hash: str
    success: bool
    halt_reason: Optional[str] = None
    halt_node: Optional[str] = None
    error: Optional[str] = None

    # ── Primary outputs ──
    synthesis: Optional[SynthesisResult] = None
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)

    # ── Execution summary ──
    total_nodes: int = 0
    executed_nodes: int = 0
    skipped_nodes: int = 0
    failed_nodes: int = 0
    elapsed_ms: float = 0.0

    # ── Full state (optional, for debugging/telemetry) ──
    node_outputs: Optional[dict[str, NodeOutput]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

---

## Appendix B: Exception Hierarchy

```python
class StrategyError(Exception):
    """Base exception for all strategy engine errors."""
    pass

class StrategySpecValidationError(StrategyError):
    """Raised when a StrategySpec fails validation at registration time."""
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(f"Spec validation failed: {'; '.join(issues)}")

class StrategyGraphCycleError(StrategyError):
    """Raised when the strategy graph contains a cycle."""
    pass

class StateViolationError(StrategyError):
    """Raised when a node attempts to violate state immutability rules."""
    pass

class ModelUnavailableError(StrategyError):
    """Raised when no model is available for a required role."""
    pass

class NodeExecutionError(StrategyError):
    """Raised when a node fails execution after all retries."""
    def __init__(self, node_id: str, node_type: str, cause: Exception):
        self.node_id = node_id
        self.node_type = node_type
        self.cause = cause
        super().__init__(f"Node '{node_id}' ({node_type}) failed: {cause}")

class ConditionEvalError(StrategyError):
    """Raised (and caught internally) when a condition expression fails."""
    pass
```

---

## Appendix C: Restricted Expression Evaluator

```python
import ast
from typing import Any

_SAFE_BUILTINS = {
    "len": len, "any": any, "all": all, "bool": bool,
    "int": int, "float": float, "str": str,
    "min": min, "max": max, "abs": abs,
    "True": True, "False": False, "None": None,
}

_ALLOWED_AST_NODES = {
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp,
    ast.Compare, ast.Call, ast.Attribute, ast.Subscript,
    ast.Name, ast.Load, ast.Constant, ast.Index, ast.Slice,
    ast.List, ast.Tuple, ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.USub, ast.UAdd,
    ast.IfExp, ast.GeneratorExp, ast.comprehension,
}


def restricted_eval(expr: str, state: Any) -> bool:
    """Evaluate a condition expression against strategy run state.

    Raises ConditionEvalError if the expression is unsafe or fails.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConditionEvalError(f"Invalid condition syntax: {e}")

    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_AST_NODES:
            raise ConditionEvalError(
                f"Disallowed AST node: {type(node).__name__} in expression: {expr}"
            )
        # Block dunder access
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ConditionEvalError(
                f"Private attribute access blocked: .{node.attr}"
            )
        # Block calls to non-whitelisted functions
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in _SAFE_BUILTINS:
                raise ConditionEvalError(
                    f"Function call not allowed: {node.func.id}()"
                )

    code = compile(tree, "<condition>", "eval")
    namespace = {"state": state, **_SAFE_BUILTINS, "__builtins__": {}}

    try:
        return bool(eval(code, namespace))
    except Exception as e:
        raise ConditionEvalError(f"Condition evaluation failed: {e}")
```
