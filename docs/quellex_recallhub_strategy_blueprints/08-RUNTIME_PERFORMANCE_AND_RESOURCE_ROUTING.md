# Blueprint 08 — Runtime Performance and Resource-Aware Routing

## 1. Objective

Improve local LLM speed and quality by adding runtime profiling, model-role routing, context budgeting, and adaptive strategy selection.

This extends the static hardware profiler with live measurements.

---

## 2. Main problem observed

The local pipeline can retrieve relevant sources quickly, but orchestration can dominate total runtime.

Typical failure pattern:

```text
retrieval: seconds
analyze/plan/evaluate/synthesize with large local model: minutes
```

The fix is not only faster hardware. The fix is better routing:

- small model or deterministic logic for classification
- retrieval before deep reasoning
- evidence cards instead of large raw context
- 26B/large model only when it materially improves output
- early exit when enough source evidence exists
- strategy-specific latency budgets

---

## 3. Model roles

Replace only `orchestrator_model` and `worker_model` with role-based models.

```python
class ModelRoleConfig(BaseModel):
    provider: str
    model: str
    purpose: str
    max_context_tokens: int
    max_output_tokens: int
    temperature: float
    local_only: bool
    expected_tokens_per_second: Optional[float]
    cost_per_million_input: Optional[float]
    cost_per_million_output: Optional[float]
```

Suggested roles:

| Role | Purpose | Typical model |
|---|---|---|
| `classifier` | intent/capability detection | small local or rules |
| `query_expander` | search query variants | small/fast model |
| `reranker` | top-result reranking | heuristic or small model |
| `evidence_compressor` | source to evidence cards | worker model |
| `synthesizer_fast` | short answer | worker or compact model |
| `synthesizer_deep` | high-quality answer | orchestrator model |
| `judge` | evaluation | separate capable model |
| `validator` | contract/citation validation | deterministic/small model |

---

## 4. Runtime profiler

Create:

```text
backend/services/runtime_profiler.py
backend/workers/runtime_profiler_worker.py
```

Measure:

- model load time
- first-token latency
- tokens/sec for prompt processing and generation if observable
- context-length sensitivity
- RAM/VRAM usage
- CPU/GPU utilization
- Ollama residency and eviction behavior
- concurrency degradation

---

## 5. Profiling test plan

Run standardized prompts:

| Test | Context | Output | Purpose |
|---|---:|---:|---|
| `tiny_classification` | 500 tokens | 100 tokens | small planning/classification speed |
| `short_rag` | 2k tokens | 500 tokens | normal fast answer |
| `evidence_synthesis` | 6k tokens | 1200 tokens | expected production legal/business answer |
| `long_context` | 32k tokens | 1500 tokens | stress test |
| `very_long_context` | 64k+ tokens | 1500 tokens | only if hardware permits |
| `parallel_workers` | N/A | N/A | concurrency performance |

---

## 6. Runtime profile schema

```python
class RuntimeModelProfile(BaseModel):
    id: str
    host_id: str
    tenant: str
    model: str
    provider: str
    quantization: Optional[str]
    context_tokens: int
    output_tokens: int
    first_token_ms: Optional[int]
    total_latency_ms: int
    tokens_per_second: float
    prompt_tokens_per_second: Optional[float]
    generation_tokens_per_second: Optional[float]
    ram_peak_gb: Optional[float]
    vram_peak_gb: Optional[float]
    cpu_avg_pct: Optional[float]
    gpu_avg_pct: Optional[float]
    success: bool
    error: Optional[str]
    created_at: datetime
```

---

## 7. Adaptive strategy selection

The StrategySelector should consider runtime profiles.

```python
if capability == "legal_hearing_questions" and hardware.local_speed["gemma4:26b"]["6k_context"] < threshold:
    choose legal_hearing_questions__fast_evidence__v1
else:
    choose legal_hearing_questions__deep_orchestrated__v1
```

Decision inputs:

- capability
- latency target
- active tenant privacy mode
- strategy historical score
- current model residency
- runtime model profile
- current resource pressure

---

## 8. Context budgeting

Add context budget enforcement before synthesis.

```python
class ContextBudgeter:
    def build_context(evidence_cards, raw_chunks, budget_tokens):
        # 1. include answer contract
        # 2. include top evidence cards
        # 3. include source anchors
        # 4. include minimal raw excerpts only when needed
        # 5. drop lower-value context with reason
```

Priority order:

1. answer contract
2. user prompt and selected interpretation
3. source policy summary
4. evidence cards
5. critical raw source spans
6. conversation context
7. optional examples/templates

Do not pass 20k tokens to the local orchestrator for a 10-question drafting task if 4k-6k evidence-card context is sufficient.

---

## 9. Fast path rules

Bypass full orchestrator when:

- one active profile/matter is clear
- top retrieval confidence is high
- capability has a template answer contract
- no external web/current facts are needed
- response is drafting/summarization based on known sources

Fast path:

```text
business_context -> retrieval -> evidence cards -> template synthesis -> validation
```

Deep path:

```text
business_context -> plan -> multi-source retrieval -> evaluate -> refine retrieval -> synthesize -> judge -> refine
```

---

## 10. Model prewarming and residency

For local/offline installs:

- prewarm selected models before scheduled runs
- avoid loading too many models simultaneously
- keep worker model resident for repeated evidence-card jobs
- run deep orchestrator only after context compression
- schedule heavyweight profiling during overnight windows

CLI:

```bash
quellexctl profiler model-test --model gemma4:26b --contexts 2k,6k,16k,32k
quellexctl profiler strategy-profile --strategy legal_hearing_questions__fast_evidence__v1 --dataset quellex_legal_core_v1
```

---

## 11. Performance gates

Each strategy should define:

```yaml
latency_target_ms: 15000
latency_hard_limit_ms: 60000
p95_latency_hard_limit_ms: 90000
max_context_tokens: 6000
max_llm_calls: 4
```

Validation should mark:

- pass
- warning
- failure
- fatal failure

---

## 12. Acceptance criteria

- Runtime profiler records model speed by context size.
- Strategy selection can use runtime profile data.
- Fast legal evidence strategy avoids large-model planning by default.
- Context budgeter limits synthesis context deterministically.
- Overnight scheduler can run performance profiling without frontend.
- Reports show quality/latency tradeoff per strategy.
