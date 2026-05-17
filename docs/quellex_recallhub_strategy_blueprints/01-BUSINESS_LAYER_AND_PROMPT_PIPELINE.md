# Blueprint 01 — Business Layer and Prompt-to-Response Pipeline

## 1. Objective

Refactor Quellex / RecallHub from a mostly generic agentic RAG flow into a business-aware prompt-to-response pipeline.

The system should understand:

- which tenant is active: `quellex`, `recallhub`, future tenant
- which business capability the user is invoking
- which source boundaries apply
- which answer format is expected
- which strategy should be used
- how much latency, model cost, and privacy exposure is acceptable

This layer should sit before the current federated agent and strategy registry.

---

## 2. Core concept: Business Capability

Add a `BusinessCapability` abstraction. It is more specific than a strategy domain.

Current domains are broad:

```text
general | software_dev | legal | hr
```

They should be expanded into practical task families:

| Tenant | Capability ID | Example user prompt | Expected output |
|---|---|---|---|
| Quellex | `legal_matter_qna` | "Was sagt der Akt zu den Fenstern?" | Source-grounded answer |
| Quellex | `legal_hearing_questions` | "Ich suche Ergänzungsfragen" | Hearing-ready question table |
| Quellex | `legal_contradiction_analysis` | "Wo widerspricht sich das Gutachten?" | Contradiction matrix |
| Quellex | `legal_source_audit` | "Welche Quellen stützen Punkt 6?" | Claim-source map |
| Quellex | `legal_submission_draft` | "Formuliere eine Stellungnahme" | Draft with caveats |
| RecallHub | `business_summary` | "Summarize these transcripts" | Executive summary |
| RecallHub | `project_lessons` | "What lessons can we use?" | Product/business insight map |
| RecallHub | `technical_architecture` | "How should we build this?" | Implementation plan |
| RecallHub | `finance_document_qna` | "How big is the minus?" | Fact answer with numbers |
| RecallHub | `customer_support_triage` | "What should I answer?" | Reply draft + rationale |

---

## 3. Business context resolver

Create a service:

```text
backend/services/business_context_resolver.py
```

### Inputs

```python
class BusinessContextInput(BaseModel):
    user_message: str
    tenant: str
    profile_key: Optional[str]
    session_id: Optional[str]
    user_id: Optional[str]
    active_matter_id: Optional[str]
    explicit_strategy_id: Optional[str]
    agent_mode: Literal["auto", "fast", "thinking"]
    privacy_mode: Literal["local_only", "hybrid", "cloud_allowed"]
    conversation_history: list[dict] = []
```

### Output

```python
class BusinessContext(BaseModel):
    tenant: str
    profile_key: Optional[str]
    active_matter_id: Optional[str]
    language: str
    capability_id: str
    capability_confidence: float
    ambiguity: dict
    source_policy: SourcePolicy
    answer_contract: AnswerContract
    quality_budget: QualityBudget
    latency_budget_ms: int
    cost_budget_eur: Optional[float]
    privacy_policy: PrivacyPolicy
    strategy_candidates: list[str]
```

---

## 4. Source policy

The pipeline must choose sources before retrieval.

```python
class SourcePolicy(BaseModel):
    primary_sources: list[str]
    secondary_sources: list[str] = []
    excluded_sources: list[str] = []
    allow_cross_profile: bool = False
    allow_cross_matter: bool = False
    allow_personal: bool = False
    allow_cloud_private: bool = False
    allow_web: bool = False
    web_requires_sanitization: bool = True
    require_source_spans: bool = True
    prefer_latest_versions: bool = True
    exclude_boilerplate: bool = True
```

### Default policies

#### Quellex legal matter default

```yaml
allow_cross_profile: false
allow_cross_matter: false
allow_web: false
allow_personal: false
require_source_spans: true
prefer_latest_versions: true
exclude_boilerplate: true
```

#### RecallHub business research default

```yaml
allow_cross_profile: true only if user has access
allow_cloud_private: true only if user owns source
allow_web: true if prompt asks for current/external facts
require_source_spans: true for internal claims
```

---

## 5. Answer contract

Every strategy execution should carry an explicit answer contract.

```python
class AnswerContract(BaseModel):
    language: str
    format_id: str
    direct_answer_required: bool = True
    output_sections: list[str]
    table_schema: Optional[list[str]] = None
    citation_granularity: Literal["none", "document", "chunk", "span", "claim"] = "span"
    unsupported_claim_policy: Literal["omit", "label", "allow_general_advice"] = "label"
    max_response_tokens: int = 1200
    tone: Literal["practical", "executive", "legal_precise", "technical"]
    must_include: list[str] = []
    must_exclude: list[str] = []
    trace_visibility: Literal["none", "operational_summary", "full_debug"] = "operational_summary"
```

### Example: Quellex hearing questions

```yaml
format_id: legal_hearing_questions_table
language: de
tone: legal_precise
table_schema:
  - priority
  - topic
  - fixation_question
  - follow_up_question
  - if_expert_evades
  - source_anchor
must_include:
  - interpretation of ambiguous terms
  - question formulations ready for oral hearing
  - source-supported technical/legal facts
  - distinction between evidence and general tactic
must_exclude:
  - generic Immobilienkauf checklist if matter context indicates litigation
  - unrelated matters
  - uncited norm claims
citation_granularity: span
unsupported_claim_policy: label
```

---

## 6. New prompt-to-response pipeline

### Current simplified flow

```text
User Query
  -> Coordinator
  -> direct response OR orchestrator
  -> analyze / plan / execute / evaluate / synthesize
```

### Proposed flow

```text
User Query
  -> BusinessContextResolver
  -> StrategySelector
  -> StrategyRunner
       1. normalize_query
       2. resolve_context
       3. select_or_confirm_source_scope
       4. retrieve
       5. rerank_and_filter
       6. build_evidence_cards
       7. synthesize_with_answer_contract
       8. validate_answer
       9. optional_refine
  -> ResponseEnvelope
  -> TelemetryEmitter
```

---

## 7. Response envelope

Return structured metadata to both UI and CLI.

```python
class AgentResponseEnvelope(BaseModel):
    response_text: str
    answer_contract_id: str
    strategy_id: str
    strategy_version: str
    strategy_spec_hash: str
    capability_id: str
    confidence: float
    sources: list[SourceReference]
    operational_trace: OperationalTraceSummary
    quality_gate: QualityGateResult
    timings: dict[str, int]
    token_usage: dict[str, int]
    warnings: list[str]
```

Do not expose chain-of-thought. Expose operational trace only.

```python
class OperationalTraceSummary(BaseModel):
    selected_interpretation: Optional[str]
    source_scope_summary: str
    retrieval_summary: str
    evidence_cards_count: int
    validation_summary: str
    omitted_reasons: list[str]
```

---

## 8. Capability detection rules

Start with a deterministic + small-model hybrid.

### Deterministic signals

| Signal | Capability |
|---|---|
| `ergänzungsfragen`, `gutachter`, `sachverständiger`, `mündliche verhandlung` | `legal_hearing_questions` |
| `widerspruch`, `gutachten`, `übersehen`, `nicht geprüft` | `legal_contradiction_analysis` |
| `schreibe`, `formuliere`, `brief`, `stellungnahme`, `email` | draft capability |
| `summary`, `lessons`, `features`, `what can we use` | `project_lessons` / `business_summary` |
| `compare`, `table`, `differences` | comparison capability |
| `latest`, `today`, `current`, `price`, `law changed` | web/current-data required |

### Small-model classification

Use a small local/cloud model only when deterministic confidence < 0.85.

```json
{
  "capability_id": "legal_hearing_questions",
  "confidence": 0.91,
  "ambiguity": {
    "is_ambiguous": true,
    "possible_interpretations": ["generic real-estate process", "specific legal proceeding"],
    "selected": "specific legal proceeding",
    "must_state_in_answer": true
  }
}
```

---

## 9. Integration with existing code

### New files

```text
backend/services/business_context_resolver.py
backend/models/business_context.py
backend/config/capabilities/*.yaml
backend/config/answer_contracts/*.yaml
backend/agent/strategy_selector.py
```

### Modified files

```text
backend/agent/coordinator.py
backend/agent/orchestrator.py
backend/agent/strategies/base.py
backend/agent/strategies/registry.py
backend/models/telemetry.py
backend/routers/strategies.py
backend/routers/prompts.py
```

### Coordinator change

```python
context = await business_context_resolver.resolve(input)
strategy = await strategy_selector.select(context)
response = await strategy_runner.run(context, strategy)
telemetry.emit(response)
return response
```

---

## 10. Acceptance criteria

- The same user prompt can be run through different strategies without code changes.
- Ambiguous prompts produce an explicit interpretation in the answer when required.
- Legal/matter prompts default to matter/profile scope and do not mix unrelated matters.
- Every response has a strategy ID, strategy version, answer contract, source policy, and quality-gate result.
- The UI and CLI can show a short operational trace without exposing internal reasoning.
- The answer format is stable across runs for the same answer contract.
