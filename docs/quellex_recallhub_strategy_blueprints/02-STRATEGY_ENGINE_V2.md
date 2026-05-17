# Blueprint 02 — Strategy Engine V2: Config-Driven DAG Execution

## 1. Objective

Extend the existing Agent Strategies system from a registry of strategy classes/prompts into a full execution framework for strategy patterns.

The new engine should allow a strategy to define:

- which pipeline nodes run
- which prompt templates are used
- which models are used for each role
- retrieval and source-scope behavior
- evidence-card behavior
- answer contract
- validation gates
- budgets for latency, context, cost, and privacy
- experiment metadata

---

## 2. Current limitation

The uploaded blueprints define:

```python
class StrategyConfig:
    max_iterations: int
    confidence_threshold: float
    early_exit_enabled: bool
    cross_search_boost: float
    content_length_penalty: float
    custom_params: dict
```

This is useful but too shallow.

A practical strategy is not just `max_iterations` and custom prompts. It is a complete execution pattern.

---

## 3. New StrategySpec model

```python
class StrategySpec(BaseModel):
    id: str
    version: str
    display_name: str
    description: str
    tenant_scope: list[str] = ["recallhub", "quellex"]
    capability_ids: list[str]
    domains: list[str]
    tags: list[str] = []
    status: Literal["draft", "active", "candidate", "deprecated", "archived"] = "draft"

    selection: StrategySelectionPolicy
    budgets: StrategyBudgets
    source_policy: SourcePolicy
    retrieval_policy: RetrievalPolicy
    evidence_policy: EvidencePolicy
    answer_contract: AnswerContract
    validation_policy: ValidationPolicy
    model_roles: dict[str, ModelRoleConfig]
    graph: StrategyGraph
    prompts: dict[str, PromptBinding]
    experiment: StrategyExperimentMetadata = StrategyExperimentMetadata()
```

---

## 4. Strategy graph

A strategy should be an executable DAG.

```python
class StrategyGraph(BaseModel):
    nodes: list[StrategyNode]
    edges: list[StrategyEdge]
    entry_node: str
    terminal_nodes: list[str]

class StrategyNode(BaseModel):
    id: str
    type: str
    enabled: bool = True
    config: dict = {}
    timeout_ms: Optional[int] = None
    retry_policy: Optional[dict] = None
    model_role: Optional[str] = None
    prompt_id: Optional[str] = None

class StrategyEdge(BaseModel):
    from_node: str
    to_node: str
    condition: Optional[str] = None
```

### Supported node types

| Node type | Purpose |
|---|---|
| `normalize_query` | Language, spelling, query cleanup, but preserve legal terms |
| `business_context` | Capability, source policy, answer contract |
| `intent_classify` | Small-model or rule-based intent classification |
| `retrieve` | Search one or more sources |
| `query_expand` | Generate alternate queries |
| `rerank` | Re-rank search results |
| `dedupe_versions` | Remove duplicate versions / prefer latest |
| `boilerplate_filter` | Remove signatures, disclaimers, repetitive chunks |
| `evidence_cards` | Compress sources into topic/fact/question cards |
| `plan` | Optional deep planning; skip in fast strategies |
| `synthesize` | Generate final answer from evidence cards/context |
| `validate_citations` | Verify citation coverage |
| `validate_contract` | Check answer shape and required sections |
| `judge_quality` | LLM/deterministic quality scoring |
| `refine` | Revise answer if quality gate fails |
| `compare_candidates` | Choose best of multiple generated answers |
| `emit_telemetry` | Persist trace and metrics |

---

## 5. Strategy budgets

```python
class StrategyBudgets(BaseModel):
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
```

Example:

```yaml
budgets:
  latency_target_ms: 15000
  latency_hard_limit_ms: 60000
  context_budget_tokens: 6000
  output_budget_tokens: 1400
  max_llm_calls: 4
  max_search_operations: 4
  local_only: true
```

---

## 6. Retrieval policy

```python
class RetrievalPolicy(BaseModel):
    search_type: Literal["semantic", "text", "hybrid", "auto"] = "hybrid"
    top_k: int = 10
    overfetch_multiplier: float = 2.0
    query_variants: int = 2
    use_query_expansion: bool = True
    use_llm_reranker: bool = False
    use_cross_encoder_reranker: bool = False
    min_score_threshold: float = 0.55
    rrf_k: int = 60
    boost_exact_entity_match: float = 1.25
    boost_same_document_family: float = 1.1
    penalize_short_chunks: bool = True
```

---

## 7. Evidence policy

```python
class EvidencePolicy(BaseModel):
    enabled: bool = True
    max_cards: int = 20
    max_cards_per_topic: int = 4
    card_schema: Literal[
        "fact_card",
        "legal_question_card",
        "contradiction_card",
        "business_insight_card",
        "technical_requirement_card"
    ]
    require_source_span: bool = True
    merge_duplicates: bool = True
    preserve_quotes_max_words: int = 25
```

### Legal question evidence card

```python
class LegalQuestionEvidenceCard(BaseModel):
    topic: str
    source_id: str
    document_title: str
    document_version: Optional[str]
    span_id: Optional[str]
    source_excerpt: str
    factual_basis: str
    legal_or_technical_issue: str
    proposed_question: str
    follow_up_question: Optional[str]
    confidence: float
```

---

## 8. Validation policy

```python
class ValidationPolicy(BaseModel):
    require_answer_contract: bool = True
    require_citation_coverage: bool = True
    min_citation_coverage: float = 0.85
    require_source_span_for_claims: bool = True
    detect_unsupported_claims: bool = True
    detect_unrelated_sources: bool = True
    detect_cross_matter_leakage: bool = True
    require_latency_budget_check: bool = True
    allow_refine_on_failure: bool = True
    max_refine_attempts: int = 1
```

---

## 9. StrategySpec example: fast legal hearing questions

```yaml
id: legal_hearing_questions_fast_evidence
version: 1.0.0
display_name: Legal Hearing Questions — Fast Evidence
status: active
tenant_scope: [quellex]
capability_ids: [legal_hearing_questions]
domains: [legal]
tags: [fast, evidence_cards, matter_scoped]

selection:
  auto_select_when:
    capability_id: legal_hearing_questions
    agent_mode: [auto, fast]
  priority: 80

budgets:
  latency_target_ms: 15000
  latency_hard_limit_ms: 45000
  context_budget_tokens: 6000
  output_budget_tokens: 1600
  max_llm_calls: 4
  max_search_operations: 4
  local_only: true

source_policy:
  primary_sources: [active_profile]
  allow_cross_matter: false
  allow_web: false
  require_source_spans: true
  prefer_latest_versions: true
  exclude_boilerplate: true

retrieval_policy:
  search_type: hybrid
  top_k: 12
  query_variants: 3
  use_query_expansion: true
  use_llm_reranker: false
  min_score_threshold: 0.60

evidence_policy:
  enabled: true
  max_cards: 16
  card_schema: legal_question_card
  require_source_span: true

answer_contract:
  format_id: legal_hearing_questions_table
  language: de
  tone: legal_precise
  citation_granularity: span
  unsupported_claim_policy: label

graph:
  entry_node: normalize
  terminal_nodes: [emit_telemetry]
  nodes:
    - { id: normalize, type: normalize_query }
    - { id: context, type: business_context }
    - { id: retrieve_exact, type: retrieve, config: { query_mode: exact_matter } }
    - { id: retrieve_concept, type: retrieve, config: { query_mode: concept } }
    - { id: filter, type: dedupe_versions }
    - { id: cards, type: evidence_cards, model_role: worker_fast }
    - { id: synthesize, type: synthesize, model_role: orchestrator_compact, prompt_id: legal_hearing_questions_synthesize }
    - { id: validate, type: validate_contract }
    - { id: emit_telemetry, type: emit_telemetry }
  edges:
    - { from_node: normalize, to_node: context }
    - { from_node: context, to_node: retrieve_exact }
    - { from_node: context, to_node: retrieve_concept }
    - { from_node: retrieve_exact, to_node: filter }
    - { from_node: retrieve_concept, to_node: filter }
    - { from_node: filter, to_node: cards }
    - { from_node: cards, to_node: synthesize }
    - { from_node: synthesize, to_node: validate }
    - { from_node: validate, to_node: emit_telemetry }
```

---

## 10. Strategy registry changes

### Current

- Python class registration
- hardcoded built-in strategies
- metrics collection

### Proposed

Support three sources:

1. Python registered strategies
2. YAML/JSON strategy specs from `backend/config/strategies/`
3. DB-stored strategy specs for experiments and user/admin-created variants

```python
class StrategyRegistry:
    async def load_all(self):
        self.load_python_strategies()
        self.load_file_strategy_specs()
        await self.load_db_strategy_specs()
        self.validate_specs()
```

---

## 11. Strategy runner

```text
backend/agent/strategy_runner.py
backend/agent/nodes/*.py
```

```python
class StrategyRunner:
    async def run(self, context: BusinessContext, spec: StrategySpec) -> AgentResponseEnvelope:
        state = StrategyRunState(context=context, spec=spec)
        graph = self.compile_graph(spec.graph)

        for node in graph.execution_order():
            if self.should_skip(node, state):
                continue
            result = await self.node_registry.execute(node, state)
            state.record_node_result(node.id, result)
            if result.halt:
                break

        return self.build_response_envelope(state)
```

---

## 12. Compatibility with current orchestrator

Do not delete the current orchestrator.

Wrap it as one possible strategy node:

```yaml
- id: legacy_orchestrator
  type: legacy_orchestrator_pipeline
  config:
    max_iterations: 3
```

This allows direct comparison:

- legacy full agent
- fast evidence strategy
- legal matter drafting strategy
- deep contradiction strategy

---

## 13. Acceptance criteria

- A new strategy can be added from YAML without Python code changes.
- A strategy can skip `analyze/plan/evaluate` and use a fast pipeline.
- A strategy can still call the legacy orchestrator as a node.
- Strategy spec validation fails on missing prompts, missing model roles, invalid node types, or unsafe source policy.
- Telemetry records include `strategy_id`, `strategy_version`, `strategy_spec_hash`, and node-level timings.
- CLI can run a strategy by ID against a test case.
