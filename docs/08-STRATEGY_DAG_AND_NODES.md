# Strategy OS — DAG Runtime & Node Catalog

Last reviewed: 2026-05-18

This blueprint documents the **execution engine** of Strategy OS: how a `StrategySpec` is compiled into a runnable DAG, how the runner advances state across topological levels, and the contract every node executor must satisfy. It also contains the catalog of all 18 built-in node types — their config keys, what state field they produce, and which fall back to rule-based behavior when no LLM is wired in.

See [07-STRATEGY_OS_OVERVIEW.md](./07-STRATEGY_OS_OVERVIEW.md) for the architectural context.

---

## 1. State Model: `StrategyRunState`

All node executors read from and write to a single typed accumulator. The state model is intentionally narrow so nodes cannot accidentally leak side channels.

```
StrategyRunState
├── Identity
│   ├── run_id: str (UUID, set by runner)
│   └── trace_id: str (UUID, set by runner, ties trace docs together)
├── Input
│   ├── query: str (caller-supplied)
│   └── normalized_query: str | None (set by normalize_query)
├── Accumulated outputs (append-only after the originating level)
│   ├── business_context: BusinessContextResult | None
│   ├── retrieved_chunks: list[RetrievedChunk]
│   ├── evidence_cards: list[EvidenceCard]
│   ├── synthesis_result: SynthesisResult | None
│   ├── validation_results: list[ValidationResult]
│   └── node_outputs: dict[node_id → NodeOutput]
└── Metadata
    ├── metadata: dict[str, Any]
    ├── elapsed_ms: float
    └── cancelled: bool
```

**Append-only invariant.** Once `synthesis_result` is set, the runner enforces that further writers can only replace it via a `refine` or `compare_candidates` node — never via an arbitrary node. The runner raises `StateViolationError` if a node tries to mutate already-populated fields outside its allowed slot. List fields (`retrieved_chunks`, `evidence_cards`, `validation_results`) are append-only across nodes; within a node the executor returns a *full replacement* list and the runner detects whether to extend or overwrite based on `node_type`.

**Per-node isolation.** Each node receives a `StateAccessor` wrapper around a deep copy of the live state, so concurrent nodes in the same topological level cannot read each other's mid-flight writes. The runner merges `NodeOutput.output_data` back into the live state only after the entire level completes.

---

## 2. Graph Model: `StrategyGraph`

A graph is a set of nodes plus directed edges:

```yaml
graph:
  entry_node: "n_normalize"
  terminal_nodes: ["n_refine"]
  nodes:
    - node_id: "n_normalize"
      node_type: "normalize_query"
      config: {}
    - node_id: "n_retrieve"
      node_type: "retrieve"
      config:
        top_k: 30
        search_type: "hybrid"
        num_candidates: 200
    # ...
  edges:
    - from_node: "n_normalize"
      to_node: "n_retrieve"
    - from_node: "n_retrieve"
      to_node: "n_evidence"
      condition: "len(retrieved_chunks) > 0"
```

**Node fields (`StrategyNode`):**

| Field | Purpose |
|---|---|
| `node_id` | Unique within the graph |
| `node_type` | Registry key — must resolve in `NodeRegistry` |
| `config` | Free-form dict passed to the executor |
| `model_role` | Logical role (`synthesizer_deep`, `worker`, etc.) the LLM helper resolves to a concrete model |
| `timeout_ms` | Per-node wall-clock timeout. The runner falls back to a 300-second default if unset |
| `optional` | If `True`, an `error` status does not halt the DAG |
| `on_empty` | `skip` (default) or `error` — controls behavior when the node produces an empty output |
| `on_error` | `skip`, `halt` (default), or `retry` — error policy |
| `retry_config` | Dict consumed when `on_error == "retry"` (max attempts, backoff) |

**Edge fields (`StrategyEdge`):**

| Field | Purpose |
|---|---|
| `from_node` | Source node id |
| `to_node` | Target node id |
| `condition` | Optional Python expression evaluated by `restricted_eval(condition, state)`. Falsy result drops the edge for this run. |

---

## 3. Graph Compilation: `compile_graph`

`backend/agent/strategy/graph_compiler.py` turns a `StrategyGraph` into a `CompiledGraph`:

```
CompiledGraph
├── adjacency_out:    node_id → [successor_ids]
├── adjacency_in:     node_id → [predecessor_ids]
├── conditional_edges: "from::to" → condition_expression
├── levels:           [[level_0_ids], [level_1_ids], …]  # topological
├── entry_node:       str
├── terminal_nodes:   [str]
└── node_map:         node_id → StrategyNode
```

Compilation enforces:

- A single `entry_node` with no incoming edges (or only conditional edges that resolve unconditionally true).
- No cycles (Kahn's algorithm detects them and raises `GraphCompilationError`).
- All node ids referenced in edges resolve.
- All `terminal_nodes` are reachable from `entry_node` along at least one edge path.
- All `node_type` values are registered in the `NodeRegistry` passed to the runner.

The resulting `levels` list is the **execution plan**: level `i+1` waits for all nodes in level `i` to finish.

---

## 4. Execution Engine: `StrategyRunner`

`StrategyRunner.run(spec, business_context, query, state_factory=None)` is the single entry point.

```python
async def run(
    self,
    spec: StrategySpec,
    business_context: BusinessContext,
    query: str,
    state_factory: Optional[Callable[[], StrategyRunState]] = None,
) -> StrategyRunResult: ...
```

**What it does, in order:**

1. Build a `StrategyRunState` (via `state_factory` or default) and stamp `run_id`, `trace_id`, `query`. Carry `business_context` into `state.business_context` if the spec does not include a `business_context` node.
2. `compile_graph(spec.graph)` → `CompiledGraph`. Failures raise `StrategyExecutionError` (wrapping `GraphCompilationError`).
3. For each topological level:
   1. Enqueue the level's nodes whose incoming edges all evaluate to truthy (unconditional edges count). Drop nodes whose every incoming path was dropped.
   2. For each enqueued node, dispatch via `NodeRegistry.execute(node, state_snapshot)` wrapped with `asyncio.wait_for(timeout_ms or 300_000)`. Run them with `asyncio.gather`.
   3. Collect `NodeOutput` per node. Apply retry policy if `output.status == "error"` and `node.on_error == "retry"`.
   4. Merge surviving outputs into the live `StrategyRunState`. Update `state.node_outputs`.
   5. Check spec budgets (`max_llm_calls`, `max_cost_usd`, `latency_hard_limit_ms`). On breach, set `halt_reason` and break out of the loop.
4. Persist a `RunTraceDoc` to `strategy_runs` via the injected `MongoRunTraceStore`. The trace records every `NodeOutput`, the resolved policy, total tokens, total LLM calls, and the final `SynthesisResult`.
5. Return a `StrategyRunResult` with `success`, `state`, `halt_reason`, `total_duration_ms`, `total_tokens`, `total_llm_calls`, `nodes_executed`, `nodes_skipped`.

**Errors and recovery:**

- `StrategyExecutionError` — unrecoverable runner error (graph compile failure, node executor crash on a non-optional + non-retryable node).
- `StateViolationError` — a node violated the append-only invariant. Always a bug.
- `ConditionEvaluationError` — a conditional edge expression failed to evaluate. The runner logs and drops the edge.
- Per-node `TimeoutError` (from `asyncio.wait_for`) becomes `NodeOutput.status == "timed_out"`. Treated like `error` unless the node is `optional`.

**Checkpointing.** If `spec.metadata.checkpoint_enabled` is truthy, the runner serializes intermediate state to the `CheckpointManager` after each level. This allows long experiments to resume on restart. Default is off.

---

## 5. Conditional Edges: `restricted_eval`

`backend/agent/strategy/condition_evaluator.py` exposes `restricted_eval(expr, state) -> bool`.

The allowed expression grammar is a strict subset of Python AST:

- Names that resolve against the live `StrategyRunState` attributes (`query`, `retrieved_chunks`, `evidence_cards`, `synthesis_result`, `validation_results`, `business_context`, `metadata`).
- Comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`.
- Boolean ops: `and`, `or`, `not`.
- Arithmetic: `+`, `-`, `*`, `/`, `%`.
- `len(...)` and attribute access.

Any other construct (function calls, dunders, attribute chains into non-state names, list comprehensions, lambdas) raises `ConditionEvaluationError`. Examples that are valid:

```yaml
condition: "len(retrieved_chunks) > 0"
condition: "synthesis_result is not None and synthesis_result.confidence > 0.6"
condition: "'high_confidence' in metadata and metadata['high_confidence']"
```

---

## 6. Node Executor Contract

All executors subclass `NodeExecutor` (`backend/agent/strategy/nodes/base.py`):

```python
class NodeExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,  # via StateAccessor — read-only deep copy
    ) -> NodeOutput: ...
```

A `NodeOutput` carries:

| Field | Set by | Notes |
|---|---|---|
| `node_id`, `node_type` | Executor | Must match the input `StrategyNode` |
| `status` | Executor | `success` / `skipped` / `empty` / `error` / `timed_out` |
| `started_at_ms`, `finished_at_ms`, `duration_ms` | Registry wrapper | Executors do not set these |
| `output_data` | Executor | The merge payload — what kind of payload depends on the node type (see table in §7) |
| `error_message` | Executor (on failure) | Free-form |
| `retry_count` | Runner | Incremented when retrying |
| `model_used`, `tokens_used` | Executor (when an LLM call is made) | Used for budget tracking |
| `timed_out` | Runner | Set when `asyncio.wait_for` raises |

**Two registration paths.** `NodeRegistry.register(node_type, executor_cls, **kwargs)` stores constructor kwargs. The first call to `get_executor(node_type)` lazily instantiates the executor as a process-singleton. LLM-capable executors are registered with `llm_helper=<NodeLLMHelper instance>`. Non-LLM executors take no kwargs.

**Rule-based fallback.** Every LLM-capable executor accepts `llm_helper=None`. When `None`, the executor produces a deterministic, rule-based output (e.g. `query_expand` returns the original query plus simple keyword permutations; `evidence_cards` derives cards directly from chunks). This lets the runner work in CI and on machines without an LLM key.

---

## 7. Node Catalog

The default registry registers 18 node types via `create_default_registry()`. Configs not listed below default to the values on the policy models on `StrategySpec` (`retrieval_policy`, `evidence_policy`, `validation_policy`).

### 7.1 Pre-retrieval nodes

| Node type | LLM | Config keys | Reads | Writes |
|---|---|---|---|---|
| `business_context` | No | (none) | `state.query`, request context | `state.business_context` |
| `normalize_query` | No | `lowercase`, `strip_punctuation`, `preserve_quotes` | `state.query` | `state.normalized_query` + `state.metadata['normalize_meta']` |
| `intent_classify` | Yes | `model_role`, `taxonomy` | `state.normalized_query` | `state.metadata['intent']`, `state.metadata['intent_confidence']` |
| `query_expand` | Yes | `model_role`, `max_variants` (default 3), `style` | `state.normalized_query`, `state.business_context` | `state.metadata['query_variants']` |
| `plan` | Yes | `model_role`, `max_steps` | `state.normalized_query`, `state.metadata['intent']` | `state.metadata['plan']` (list of task dicts) |

### 7.2 Retrieval and curation nodes

| Node type | LLM | Config keys | Reads | Writes |
|---|---|---|---|---|
| `retrieve` | No | `top_k`, `search_type` (`semantic`/`text`/`hybrid`), `min_score`, `num_candidates`, `rrf_constant` | `state.normalized_query`, `state.metadata['query_variants']`, `state.business_context.resolved_source_policy` | `state.retrieved_chunks` |
| `rerank` | Yes | `model_role`, `top_k`, `reranker` (`llm`/`cross_encoder`) | `state.retrieved_chunks` | `state.retrieved_chunks` (reordered, possibly truncated) |
| `dedupe_versions` | No | `prefer_latest_versions`, `family_key` | `state.retrieved_chunks` | `state.retrieved_chunks` (deduped) |
| `boilerplate_filter` | No | `patterns`, `min_substantive_ratio` | `state.retrieved_chunks` | `state.retrieved_chunks` (filtered) + appends `OmittedResultEntry` to `state.metadata['omitted_results']` |
| `evidence_cards` | Yes | `model_role`, `card_types`, `max_cards`, `min_confidence`, `preserve_quotes_max_words` | `state.retrieved_chunks` | `state.evidence_cards` |

### 7.3 Synthesis nodes

| Node type | LLM | Config keys | Reads | Writes |
|---|---|---|---|---|
| `synthesize` | Yes | `model_role`, `answer_contract_id` override, `max_context_tokens`, `max_output_tokens`, `format_override`, `tone_override` | `state.evidence_cards` (or `state.retrieved_chunks` if no cards), `state.business_context.answer_contract` | `state.synthesis_result` |
| `refine` | Yes | `model_role`, `max_output_tokens`, `focus_on` (list of issue types to fix) | `state.synthesis_result`, `state.validation_results` | `state.synthesis_result` (replaced) |
| `compare_candidates` | Yes | `model_role`, `candidate_count`, `selection_criteria` | `state.metadata['candidate_syntheses']` (set by an upstream fan-out) | `state.synthesis_result` (best one) |

### 7.4 Validation nodes

| Node type | LLM | Config keys | Reads | Writes |
|---|---|---|---|---|
| `validate_contract` | No | (uses `spec.answer_contract`) | `state.synthesis_result`, `state.business_context.answer_contract` | Appends `ValidationResult` to `state.validation_results` |
| `validate_citations` | No | `strictness` (`strict`/`relaxed`), `min_quote_overlap` | `state.synthesis_result`, `state.retrieved_chunks` | Appends `ValidationResult` to `state.validation_results` |
| `judge_quality` | Yes | `model_role`, `scoring_profile` | `state.synthesis_result`, `state.retrieved_chunks`, `state.evidence_cards` | `state.metadata['quality_scores']` (per-dimension dict) |

### 7.5 Bridge and observability nodes

| Node type | LLM | Config keys | Reads | Writes |
|---|---|---|---|---|
| `emit_telemetry` | No | `level`, `payload_keys` | Entire state | Calls `TelemetryService.emit(...)` — does not mutate state |
| `legacy_orchestrator_pipeline` | Indirect (delegates) | `rag_config`, `eval_config` | `state.query` (plus `business_context`) | `state.synthesis_result` + `state.retrieved_chunks` populated from legacy pipeline output |

The `legacy_orchestrator_pipeline` node is the bridge that lets a spec embed the entire pre-Strategy-OS orchestrator inside a single DAG node — useful for incremental adoption and for A/B-ing legacy vs new in evaluation.

---

## 8. Example: The Bundled Specs

Two strategies ship in `backend/config/strategy_specs/` and are auto-seeded on startup when `strategy_spec_auto_seed = True`.

### 8.1 `deep_orchestrated_v1`

8-node DAG for deep, validated, refined answers:

```
normalize_query → query_expand → retrieve(top_k=30) → rerank(top_k=20)
              → evidence_cards(max=20) → synthesize → validate_citations → refine
```

Budgets: 15s target, 60s hard limit, 32k context, 30 LLM calls, $0.50.

### 8.2 `fast_evidence_v1`

Lean DAG for low-latency, evidence-grounded answers (synthesize + evidence_cards only, no rerank, no refine).

Open the YAMLs to see the exact configs — they are the canonical examples of how to author a spec.

---

## 9. Authoring a New Node Type

1. Create `backend/agent/strategy/nodes/<your_node>.py` and subclass `NodeExecutor`.
2. Implement `async execute(node, state) -> NodeOutput`. Read inputs from `state` via the typed attributes; build the merge payload in `output_data`.
3. If your node calls an LLM, accept `llm_helper: NodeLLMHelper | None` in `__init__` and fall back to deterministic behavior when `None`.
4. Register the node type in `create_default_registry()` (`backend/agent/strategy/nodes/registry.py`). Decide whether it belongs in `_plain_nodes` (no LLM) or `_llm_nodes` (LLM-capable).
5. Add unit tests under `backend/tests/strategy/nodes/` that cover both the LLM and rule-based fallback paths.
6. Document the node type by appending a row to §7 of this blueprint.

---

## 10. Read Next

- For spec lifecycle, capability detection, answer contracts, and source policies → [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md)
- For evaluation and judging of synthesized outputs → [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md)
- For the scheduler that uses this runtime to compare specs overnight → [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md)
