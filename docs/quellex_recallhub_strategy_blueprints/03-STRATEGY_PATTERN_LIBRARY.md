# Blueprint 03 — Initial Strategy Pattern Library

## 1. Objective

Create a library of reusable strategy patterns. Each pattern should be implemented as a StrategySpec template with parameters.

The goal is not to guess one perfect agent behavior. The goal is to make many useful behaviors testable and comparable.

---

## 2. Pattern naming convention

```text
{domain_or_capability}__{pattern}__{variant}__v{major}
```

Examples:

```text
legal_hearing_questions__fast_evidence__local_v1
legal_hearing_questions__deep_orchestrated__local_v1
business_summary__map_reduce__hybrid_v1
technical_architecture__planner_synthesizer__cloud_v1
```

---

## 3. Pattern categories

| Category | Pattern | Purpose |
|---|---|---|
| Fast RAG | `fast_hybrid_rag` | Low-latency source-grounded answer |
| Evidence | `evidence_cards` | Compress retrieval into verifiable cards |
| Legal | `legal_matter_drafting` | Source-grounded legal-style drafts/questions |
| Legal | `contradiction_matrix` | Find inconsistencies, omissions, method gaps |
| Source audit | `claim_source_map` | Map claims/questions to sources |
| Deep research | `multi_step_orchestrated` | Complex/multi-hop queries |
| Summarization | `map_reduce_summary` | Large corpus/transcript summaries |
| Comparison | `comparison_matrix` | Compare products, vendors, docs, positions |
| Ensemble | `generate_judge_select` | Generate two answers and judge/select/refine |
| Retrieval test | `retrieval_only` | Debug search without synthesis |
| Minimal | `no_llm_search_answer` | Deterministic extractive answer for facts |
| Local privacy | `local_only_conservative` | No cloud calls, strict source boundaries |

---

## 4. Built-in strategy specs to implement first

### 4.1 `general__fast_hybrid_rag__v1`

**Use when:** Generic source-grounded Q&A with low complexity.

```text
normalize -> business_context -> retrieve(hybrid) -> rerank(light) -> synthesize(short) -> validate_contract -> telemetry
```

Settings:

```yaml
latency_target_ms: 8000
context_budget_tokens: 4000
query_variants: 1
top_k: 6
max_llm_calls: 2
```

Acceptance:

- returns direct answer in same language
- cites sources at document/chunk level
- under 10 seconds on cloud/hybrid and under 30 seconds local on typical Kanzlei hardware

---

### 4.2 `legal_matter__fast_evidence__v1`

**Use when:** Legal/matter prompt where active profile/matter contains the answer.

```text
normalize -> business_context -> retrieve_exact + retrieve_concept -> matter_filter -> version_dedupe -> evidence_cards -> synthesize -> citation_validate -> telemetry
```

Key policies:

```yaml
allow_web: false
allow_cross_matter: false
prefer_latest_versions: true
require_source_spans: true
```

Acceptance:

- no unrelated matter sources
- if query is ambiguous, states selected interpretation
- produces answer from evidence cards, not raw oversized context

---

### 4.3 `legal_hearing_questions__fast_evidence__v1`

**Use when:** User asks for Ergänzungsfragen, Nachhakfragen, questions for oral hearing, Gutachterbefragung.

Answer format:

| Priority | Topic | Festlegungsfrage | Nachhakfrage | Wenn der Gutachter ausweicht | Source |
|---|---|---|---|---|---|

Special logic:

- force question pattern: yes/no first, then method/source/norm follow-up
- distinguish source-supported question from general hearing tactic
- reject generic real-estate purchase checklist if matter evidence indicates litigation

---

### 4.4 `legal_contradiction__matrix__v1`

**Use when:** User asks for contradictions, weak points, omissions, missing checks.

Answer format:

| Issue | Source statement A | Source statement B / omission | Why this matters | Proposed question | Source anchors |
|---|---|---|---|---|---|

Pipeline:

```text
retrieve issue-specific docs -> build contradiction cards -> synthesize matrix -> validate every contradiction has at least two anchors or one anchor + explicit omission label
```

Acceptance:

- no invented contradictions
- omissions are labeled as omissions, not contradictions
- source anchors required

---

### 4.5 `legal_source_audit__claim_map__v1`

**Use when:** User wants verification, source tracing, or lawyer handover.

Answer format:

| Claim / Draft question | Supported? | Source anchor | Confidence | Notes |
|---|---|---|---|---|

This pattern is also useful as a validation step for other legal strategies.

---

### 4.6 `business_insights__transcript_mining__v1`

**Use when:** User asks what information, lessons, features, or experiences can be extracted from transcripts/documents.

Pipeline:

```text
retrieve broad -> cluster themes -> evidence cards -> insight synthesis -> action backlog -> validate against sources
```

Answer sections:

1. Executive summary
2. Product improvements
3. Business/process improvements
4. Sales/marketing opportunities
5. Risks/anti-patterns
6. Concrete backlog items

---

### 4.7 `technical_architecture__planner_synthesizer__v1`

**Use when:** User asks for implementation architecture, app blueprints, technical prompts.

Pipeline:

```text
business_context -> retrieve relevant project docs -> architecture planner -> implementation blueprint synthesis -> acceptance tests -> file output option
```

Required sections:

- target architecture
- files to create/modify
- data model/API
- implementation phases
- tests
- acceptance criteria

---

### 4.8 `comparison__matrix__v1`

**Use when:** User asks to compare products, providers, strategies, models, documents.

Pipeline:

```text
entity extraction -> parallel retrieval per entity -> normalize attributes -> comparison table -> recommendation -> caveats
```

Acceptance:

- every comparison row must be supported by source references or explicitly marked as inference
- no asymmetrical sourcing: do not deeply source A and weakly source B unless noted

---

### 4.9 `ensemble__two_answers_judge_select__v1`

**Use when:** High-stakes or complex query where answer quality matters more than latency.

Pipeline:

```text
retrieve -> evidence_cards -> synthesize_candidate_A(fast/template) -> synthesize_candidate_B(deep/orchestrated) -> blind_judge -> final_refine -> telemetry
```

Acceptance:

- judge must not see strategy IDs
- final answer should state uncertainty when evidence is incomplete
- expensive strategy; not default for fast mode

---

### 4.10 `debug__retrieval_only__v1`

**Use when:** Developers need to inspect retrieval quality.

Output:

```text
- normalized query
- source policy
- query variants
- raw top results
- reranked top results
- excluded results with reason
- duplicate/version groups
- boilerplate filtered chunks
```

This is critical for diagnosing issues like unrelated matter leakage and boilerplate matching.

---

## 5. Strategy variant dimensions for automatic exploration

The scheduler should be able to mutate the following dimensions:

| Dimension | Example values |
|---|---|
| Retrieval type | semantic, text, hybrid, hybrid+exact boost |
| Query variants | 1, 2, 3, 5 |
| Top-K | 5, 8, 12, 20 |
| Reranker | none, heuristic, small LLM, cross-encoder |
| Context style | raw chunks, evidence cards, compressed summaries |
| Synthesis model | worker, orchestrator, cloud model, local model |
| Planning depth | none, lightweight, full orchestrator |
| Validation | none, citation-only, contract+citations, judge+citations |
| Answer format | direct, table, legal table, executive summary |
| Source scope | active matter, active profile, all allowed, web allowed |
| Latency budget | fast, balanced, deep |

---

## 6. Strategy library folder structure

```text
backend/config/strategies/
  general/
    general__fast_hybrid_rag__v1.yaml
  legal/
    legal_matter__fast_evidence__v1.yaml
    legal_hearing_questions__fast_evidence__v1.yaml
    legal_hearing_questions__deep_orchestrated__v1.yaml
    legal_contradiction__matrix__v1.yaml
    legal_source_audit__claim_map__v1.yaml
  business/
    business_insights__transcript_mining__v1.yaml
  technical/
    technical_architecture__planner_synthesizer__v1.yaml
  debug/
    debug__retrieval_only__v1.yaml
```

---

## 7. Acceptance criteria

- At least 8 strategy specs exist and validate successfully.
- Every strategy has a documented answer contract.
- Every strategy can be executed from CLI against a test case.
- Every strategy produces telemetry with comparable metrics.
- The strategy scheduler can generate variants from at least 5 dimensions.
