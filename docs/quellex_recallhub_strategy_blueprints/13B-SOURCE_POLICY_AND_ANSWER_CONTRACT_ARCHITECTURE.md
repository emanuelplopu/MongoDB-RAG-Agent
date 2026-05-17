# Blueprint 13B — Source Policy and Answer Contract Architecture

> **Addendum to:** Blueprint 01 (Business Layer and Prompt Pipeline), Blueprint 11 (Security, Privacy, and Tenant Governance)
>
> **Resolves:** Specification gaps B1-1, B1-2, B1-3, B1-4, UX-3, UX-4

---

## 1. Source Policy Enforcement Architecture

### 1.1 Policy Sources

Three independent sources contribute to the final source policy:

| # | Source | Location | Scope |
|---|--------|----------|-------|
| 1 | **Tenant defaults** | `backend/config/tenant_strategy_policies/{tenant}.yaml` | Floor — minimum restrictions all strategies inherit |
| 2 | **Profile-level config** | `profiles.yaml` → `source_policy` section per profile key | Overrides tenant defaults where stricter |
| 3 | **StrategySpec.source_policy** | `strategy_specs` MongoDB collection | Per-strategy overrides; can only restrict further |

### 1.2 Resolution Semantics: Intersection

The merge operation is **intersection** (most restrictive wins). A StrategySpec can **restrict** but **never expand** what tenant and profile policy already permit.

```python
# Resolution rule: For boolean flags, result = tenant AND profile AND strategy
# For list fields, result = intersection of all non-empty sets
# If any layer sets allow_web=false, final allow_web=false regardless of other layers
```

### 1.3 Pydantic Models

```python
from typing import Optional
from pydantic import BaseModel, Field


class SourcePolicy(BaseModel):
    """Raw source policy from a single layer (tenant, profile, or strategy)."""

    primary_sources: list[str] = Field(default_factory=list)
    secondary_sources: list[str] = Field(default_factory=list)
    excluded_sources: list[str] = Field(default_factory=list)
    allowed_profile_ids: Optional[list[str]] = None  # None = defer to higher layer
    allowed_matter_ids: Optional[list[str]] = None
    allow_cross_profile: Optional[bool] = None
    allow_cross_matter: Optional[bool] = None
    allow_personal: Optional[bool] = None
    allow_cloud_private: Optional[bool] = None
    allow_web: Optional[bool] = None
    web_requires_sanitization: Optional[bool] = None
    require_source_spans: Optional[bool] = None
    prefer_latest_versions: Optional[bool] = None
    exclude_boilerplate: Optional[bool] = None


class ResolvedSourcePolicy(BaseModel):
    """Fully resolved source policy after intersection of all layers."""

    primary_sources: list[str]
    secondary_sources: list[str]
    excluded_sources: list[str]
    allowed_profile_ids: list[str]
    allowed_matter_ids: list[str]
    allow_cross_profile: bool = False
    allow_cross_matter: bool = False
    allow_personal: bool = False
    allow_cloud_private: bool = False
    allow_web: bool = False
    web_requires_sanitization: bool = True
    require_source_spans: bool = True
    prefer_latest_versions: bool = True
    exclude_boilerplate: bool = True
    resolved_from: list[str] = Field(
        default_factory=list,
        description="Layer names that contributed: ['tenant', 'profile', 'strategy']"
    )


class SearchRequest(BaseModel):
    """Search request with enforced source boundaries."""

    query: str
    query_embedding: Optional[list[float]] = None
    allowed_profile_ids: list[str]
    allowed_matter_ids: list[str]
    excluded_matter_ids: list[str] = Field(default_factory=list)
    allow_personal: bool = False
    allow_web: bool = False
    allow_cloud_private: bool = False
    excluded_sources: list[str] = Field(default_factory=list)
    prefer_latest_versions: bool = True
    exclude_boilerplate: bool = True
    max_results: int = 20
    num_candidates: int = 400
    min_score: float = 0.0
```

### 1.4 Resolution Algorithm

```python
def resolve_source_policy(
    tenant_policy: SourcePolicy,
    profile_policy: Optional[SourcePolicy],
    strategy_policy: Optional[SourcePolicy],
    user_accessible_profiles: list[str],
    active_matter_id: Optional[str],
) -> ResolvedSourcePolicy:
    """
    Intersection-based resolution.
    
    Rules:
      - Boolean flags: result = ALL layers that define the flag must agree (AND).
        If any layer explicitly sets False, result is False.
      - List fields (allowed_profile_ids, allowed_matter_ids):
        result = intersection of all non-None layers, clamped to user access.
      - excluded_sources: union of all layers (additive exclusion).
    """
    layers = [tenant_policy]
    resolved_from = ["tenant"]
    if profile_policy:
        layers.append(profile_policy)
        resolved_from.append("profile")
    if strategy_policy:
        layers.append(strategy_policy)
        resolved_from.append("strategy")

    # --- Boolean intersection ---
    def resolve_bool(field: str, default: bool) -> bool:
        values = [getattr(layer, field) for layer in layers if getattr(layer, field) is not None]
        if not values:
            return default
        return all(values)  # AND semantics

    # --- List intersection ---
    def resolve_id_list(field: str, universe: list[str]) -> list[str]:
        sets = [
            set(getattr(layer, field))
            for layer in layers
            if getattr(layer, field) is not None
        ]
        if not sets:
            return universe
        result = sets[0]
        for s in sets[1:]:
            result = result.intersection(s)
        # Clamp to user's actual access
        return list(result.intersection(set(universe)))

    # --- Excluded sources: union ---
    all_excluded = set()
    for layer in layers:
        all_excluded.update(layer.excluded_sources)

    return ResolvedSourcePolicy(
        primary_sources=layers[0].primary_sources,  # Tenant defines primary
        secondary_sources=layers[0].secondary_sources,
        excluded_sources=list(all_excluded),
        allowed_profile_ids=resolve_id_list("allowed_profile_ids", user_accessible_profiles),
        allowed_matter_ids=resolve_id_list(
            "allowed_matter_ids",
            [active_matter_id] if active_matter_id else []
        ),
        allow_cross_profile=resolve_bool("allow_cross_profile", False),
        allow_cross_matter=resolve_bool("allow_cross_matter", False),
        allow_personal=resolve_bool("allow_personal", False),
        allow_cloud_private=resolve_bool("allow_cloud_private", False),
        allow_web=resolve_bool("allow_web", False),
        web_requires_sanitization=resolve_bool("web_requires_sanitization", True),
        require_source_spans=resolve_bool("require_source_spans", True),
        prefer_latest_versions=resolve_bool("prefer_latest_versions", True),
        exclude_boilerplate=resolve_bool("exclude_boilerplate", True),
        resolved_from=resolved_from,
    )
```

### 1.5 Pipeline Integration

```text
BusinessContextResolver.resolve(input)
  │
  ├── 1. Load tenant_policy from YAML
  ├── 2. Load profile_policy from profiles.yaml (if profile active)
  ├── 3. Load strategy_policy from StrategySpec (if strategy selected)
  ├── 4. Call resolve_source_policy() → ResolvedSourcePolicy
  ├── 5. Merge into SearchRequest
  │
  └── Return BusinessContext with resolved source_policy
           │
           ▼
      FederatedSearch.search(search_request)
           │
           ├── Layer 1: Pre-retrieval enforcement
           ├── Layer 2: Post-retrieval validation
           └── Layer 3: Synthesis boundary reminder
```

### 1.6 Enforcement Layers

#### Layer 1 — Pre-Retrieval (Hard Enforcement)

Applied in `FederatedSearch` before any database query executes:

```python
async def search(self, request: SearchRequest) -> list[SearchResult]:
    # MongoDB $match conditions from policy
    match_conditions = {
        "profile_id": {"$in": request.allowed_profile_ids},
    }
    if request.allowed_matter_ids:
        match_conditions["matter_id"] = {"$in": request.allowed_matter_ids}
    if request.excluded_sources:
        match_conditions["source_id"] = {"$nin": request.excluded_sources}

    # Web search gating
    if not request.allow_web:
        # Web search provider is never invoked
        skip_web = True

    # Personal data gating
    if not request.allow_personal:
        match_conditions["access_type"] = {"$ne": "personal"}

    # Execute vector search with pre-filter
    pipeline = [
        {"$vectorSearch": {
            "queryVector": request.query_embedding,
            "path": "embedding",
            "numCandidates": request.num_candidates,
            "limit": request.max_results,
            "filter": match_conditions,
        }}
    ]
    ...
```

#### Layer 2 — Post-Retrieval Validation

A validator node runs after retrieval and before evidence card construction:

```python
def validate_results_against_policy(
    results: list[SearchResult],
    policy: ResolvedSourcePolicy,
) -> tuple[list[SearchResult], list[OmittedResult]]:
    """
    Second-pass validation. Catches any results that leaked through
    pre-filter (e.g., due to index lag or cross-collection joins).
    """
    valid = []
    omitted = []
    for result in results:
        if result.profile_id not in policy.allowed_profile_ids:
            omitted.append(OmittedResult(result=result, reason="cross_profile_blocked"))
        elif result.matter_id and result.matter_id not in policy.allowed_matter_ids:
            omitted.append(OmittedResult(result=result, reason="cross_matter_blocked"))
        elif result.source_id in policy.excluded_sources:
            omitted.append(OmittedResult(result=result, reason="forbidden_source"))
        elif result.access_type == "personal" and not policy.allow_personal:
            omitted.append(OmittedResult(result=result, reason="personal_data_blocked"))
        elif result.access_type == "web" and not policy.allow_web:
            omitted.append(OmittedResult(result=result, reason="web_blocked"))
        else:
            valid.append(result)
    return valid, omitted
```

#### Layer 3 — Synthesis Boundary Reminder

The synthesis prompt includes an explicit source boundary instruction:

```text
IMPORTANT: Your answer MUST only reference information from the provided evidence cards.
Do NOT include information from sources outside: {allowed_profile_ids}.
Do NOT reference matters other than: {allowed_matter_ids}.
```

> **Note:** Layer 3 is a defense-in-depth measure. Enforcement does NOT rely on prompt compliance. Layers 1 and 2 are the authoritative enforcement points.

### 1.7 Examples

#### Quellex Legal (Strict)

```yaml
# Tenant default: quellex.yaml
allow_cross_profile: false
allow_cross_matter: false
allow_web: false
allow_personal: false
allow_cloud_private: false
require_source_spans: true
exclude_boilerplate: true

# Profile override: test-law
allowed_profile_ids: ["test-law"]
allowed_matter_ids: ["matter-2024-fenster"]

# Strategy: legal_hearing_questions
# No relaxation possible — intersection keeps everything locked
```

**Result:** Only documents from profile `test-law`, matter `matter-2024-fenster`, no web, no personal.

#### RecallHub Business (Permissive)

```yaml
# Tenant default: recallhub.yaml
allow_cross_profile: true
allow_web: true
allow_cloud_private: true
allow_personal: false
require_source_spans: true

# Profile override: parhelion-energy
allowed_profile_ids: ["parhelion-energy", "shared-knowledge"]

# Strategy: business_summary
allow_web: true
allow_cross_profile: true
```

**Result:** Documents from `parhelion-energy` + `shared-knowledge`, web search enabled, cloud private enabled, no personal data.

---

## 2. AnswerContract Resolution Protocol

### 2.1 Storage

Answer contracts are stored in the `answer_contracts` MongoDB collection with `format_id` as the primary lookup key and `version` for history:

```javascript
// MongoDB collection: answer_contracts
// Index: { format_id: 1, version: -1 } (unique)
{
  "format_id": "legal_hearing_questions_table",
  "version": 3,
  "language": "de",
  "output_sections": [...],
  "table_schema": {...},
  "tone": "legal_precise",
  "citation_granularity": "span",
  "unsupported_claim_policy": "label",
  "required_fields": ["priority", "topic", "fixation_question", "source_anchor"],
  "max_length_tokens": 2400,
  "created_at": ISODate("2026-03-15T10:00:00Z"),
  "updated_at": ISODate("2026-04-01T14:30:00Z")
}
```

### 2.2 Pydantic Models

```python
from typing import Optional, Literal
from pydantic import BaseModel, Field


class ColumnDef(BaseModel):
    """Column definition for table schema."""

    column_id: str
    title: str
    type: Literal["text", "number", "boolean", "enum", "reference"]
    required: bool = True
    description: str = ""
    enum_values: Optional[list[str]] = None
    max_length: Optional[int] = None


class TableSchema(BaseModel):
    """Defines structured tabular output within a section."""

    columns: list[ColumnDef]
    row_source: str = Field(
        description="Where rows come from: 'per_claim', 'per_document', 'per_topic', 'per_question'"
    )
    sort_by: Optional[str] = Field(
        default=None,
        description="Column ID to sort by, or None for relevance order"
    )
    min_rows: int = 1
    max_rows: Optional[int] = None


class OutputSection(BaseModel):
    """Defines one section of the structured answer output."""

    section_id: str
    title: str
    required: bool = True
    format: Literal["prose", "list", "table", "structured"]
    description: str = Field(
        description="Instructions for the LLM on what this section should contain"
    )
    table_schema: Optional[TableSchema] = None
    max_length_tokens: Optional[int] = None


class AnswerContract(BaseModel):
    """Full answer contract defining expected response structure."""

    format_id: str
    version: int
    language: str
    output_sections: list[OutputSection]
    table_schema: Optional[TableSchema] = Field(
        default=None,
        description="Top-level table schema when the entire response is tabular"
    )
    tone: Literal["practical", "executive", "legal_precise", "technical", "neutral"]
    citation_granularity: Literal["none", "document", "chunk", "span", "claim"] = "span"
    unsupported_claim_policy: Literal["omit", "label", "allow_general_advice"] = "label"
    required_fields: list[str] = Field(default_factory=list)
    max_length_tokens: Optional[int] = None
    must_include: list[str] = Field(default_factory=list)
    must_exclude: list[str] = Field(default_factory=list)
    trace_visibility: Literal["none", "operational_summary", "full_debug"] = "operational_summary"
```

### 2.3 Relationship: output_sections vs table_schema

- **`output_sections`** defines the document-level structure (e.g., "Summary", "Detailed Analysis", "Recommendations").
- **`table_schema`** (top-level) defines a global tabular format when the entire response is a table.
- **`OutputSection.table_schema`** defines a table within a specific section.

These are complementary: a response can have prose sections and one section containing a table.

### 2.4 Resolution Flow

```python
async def resolve_answer_contract(
    capability_id: str,
    strategy_spec: Optional[StrategySpec],
    db: AsyncIOMotorDatabase,
) -> AnswerContract:
    """
    Resolution order:
      1. If strategy_spec pins a format_id + version → load exact version
      2. If strategy_spec pins only format_id → load latest version
      3. Map capability_id to default format_id → load latest version
      4. Fallback: load from YAML defaults
    """
    # Step 1/2: Strategy-pinned contract
    if strategy_spec and strategy_spec.answer_contract_format_id:
        query = {"format_id": strategy_spec.answer_contract_format_id}
        if strategy_spec.answer_contract_version:
            query["version"] = strategy_spec.answer_contract_version
        
        doc = await db.answer_contracts.find_one(
            query, sort=[("version", -1)]
        )
        if doc:
            return AnswerContract(**doc)

    # Step 3: Capability default mapping
    default_format_id = CAPABILITY_TO_FORMAT.get(capability_id)
    if default_format_id:
        doc = await db.answer_contracts.find_one(
            {"format_id": default_format_id},
            sort=[("version", -1)]
        )
        if doc:
            return AnswerContract(**doc)

    # Step 4: YAML fallback
    return load_yaml_answer_contract(capability_id)


# Capability → default format_id mapping
CAPABILITY_TO_FORMAT: dict[str, str] = {
    "legal_hearing_questions": "legal_hearing_questions_table",
    "legal_contradiction_analysis": "legal_contradiction_matrix",
    "legal_matter_qna": "legal_matter_answer",
    "legal_source_audit": "legal_source_audit_map",
    "legal_submission_draft": "legal_draft_document",
    "business_summary": "business_summary_report",
    "project_lessons": "business_lessons_report",
    "technical_architecture": "technical_architecture_plan",
    "finance_document_qna": "finance_fact_answer",
    "customer_support_triage": "support_reply_draft",
}
```

### 2.5 Version Resolution

| Scenario | Behavior |
|----------|----------|
| Strategy pins `format_id` + `version` | Load exact version; fail if not found |
| Strategy pins `format_id` only | Load highest `version` for that `format_id` |
| No strategy pin, capability mapped | Load latest version of mapped `format_id` |
| No match anywhere | Load YAML fallback for `general_fast_rag` |

### 2.6 YAML Examples

#### `legal_hearing_questions_table`

```yaml
format_id: legal_hearing_questions_table
version: 3
language: de
tone: legal_precise
citation_granularity: span
unsupported_claim_policy: label
max_length_tokens: 2400
required_fields:
  - priority
  - topic
  - fixation_question
  - source_anchor
output_sections:
  - section_id: context_summary
    title: "Zusammenfassung des Sachverhalts"
    required: true
    format: prose
    description: "Brief factual summary of the matter context relevant to the hearing"
    max_length_tokens: 300
  - section_id: questions_table
    title: "Fragenkatalog"
    required: true
    format: table
    description: "Table of hearing questions ordered by priority"
    table_schema:
      columns:
        - column_id: priority
          title: "Priorität"
          type: enum
          enum_values: ["hoch", "mittel", "niedrig"]
          required: true
        - column_id: topic
          title: "Thema"
          type: text
          required: true
          max_length: 100
        - column_id: fixation_question
          title: "Beweisfrage"
          type: text
          required: true
        - column_id: follow_up_question
          title: "Nachfrage"
          type: text
          required: false
        - column_id: if_expert_evades
          title: "Falls Gutachter ausweicht"
          type: text
          required: false
        - column_id: source_anchor
          title: "Quellenverankerung"
          type: reference
          required: true
      row_source: per_topic
      sort_by: priority
  - section_id: tactical_notes
    title: "Taktische Hinweise"
    required: false
    format: list
    description: "Optional tactical notes for the hearing"
```

#### `legal_contradiction_matrix`

```yaml
format_id: legal_contradiction_matrix
version: 2
language: de
tone: legal_precise
citation_granularity: span
unsupported_claim_policy: label
max_length_tokens: 3000
required_fields:
  - claim_a
  - claim_b
  - contradiction_type
  - source_refs
output_sections:
  - section_id: overview
    title: "Überblick der Widersprüche"
    required: true
    format: prose
    description: "Executive summary of contradictions found"
    max_length_tokens: 200
  - section_id: contradiction_table
    title: "Widerspruchsmatrix"
    required: true
    format: table
    description: "Matrix comparing contradicting claims with sources"
    table_schema:
      columns:
        - column_id: claim_a
          title: "Aussage A"
          type: text
          required: true
        - column_id: source_a
          title: "Quelle A"
          type: reference
          required: true
        - column_id: claim_b
          title: "Aussage B (widerspricht)"
          type: text
          required: true
        - column_id: source_b
          title: "Quelle B"
          type: reference
          required: true
        - column_id: contradiction_type
          title: "Art des Widerspruchs"
          type: enum
          enum_values: ["faktisch", "temporal", "methodisch", "bewertend"]
          required: true
        - column_id: significance
          title: "Relevanz"
          type: enum
          enum_values: ["kritisch", "erheblich", "marginal"]
          required: true
      row_source: per_claim
      sort_by: significance
  - section_id: recommendations
    title: "Empfehlungen"
    required: false
    format: list
    description: "Recommended actions based on contradictions found"
```

#### `business_summary_report`

```yaml
format_id: business_summary_report
version: 2
language: en
tone: executive
citation_granularity: document
unsupported_claim_policy: allow_general_advice
max_length_tokens: 1500
required_fields:
  - key_findings
  - action_items
output_sections:
  - section_id: executive_summary
    title: "Executive Summary"
    required: true
    format: prose
    description: "2-3 sentence summary of the most important findings"
    max_length_tokens: 200
  - section_id: key_findings
    title: "Key Findings"
    required: true
    format: list
    description: "Bullet-point list of key findings from the source material"
  - section_id: detailed_analysis
    title: "Detailed Analysis"
    required: false
    format: prose
    description: "Deeper analysis connecting findings to business context"
    max_length_tokens: 600
  - section_id: action_items
    title: "Recommended Actions"
    required: true
    format: list
    description: "Prioritized list of recommended next steps"
  - section_id: sources_summary
    title: "Sources"
    required: true
    format: structured
    description: "List of documents referenced with relevance notes"
```

---

## 3. Ambiguity Resolution Protocol

### 3.1 Confidence Thresholds

| Confidence Range | Action | Response Behavior |
|-----------------|--------|-------------------|
| >= 0.85 | Proceed with detected capability | No ambiguity note in response |
| 0.60 – 0.84 | Proceed with detected capability | Include `ambiguity_note` explaining interpretation |
| < 0.60 | Do not proceed | Set `clarification_needed: true`, suggest alternatives |
| 0.0 (no match) | Safe fallback | Use `general_fast_rag`, no clarification prompt |

### 3.2 Ambiguity in Response Envelope

```python
class AmbiguityResolution(BaseModel):
    """Ambiguity detection and resolution metadata in response envelope."""

    ambiguity_detected: bool
    confidence: float = Field(ge=0.0, le=1.0)
    interpreted_as: str = Field(description="The capability_id selected")
    alternative_interpretations: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 alternative capability_ids considered"
    )
    clarification_needed: bool = False
    clarification_prompt: Optional[str] = Field(
        default=None,
        description="User-facing question if clarification_needed=true"
    )
    ambiguity_note: Optional[str] = Field(
        default=None,
        description="Included in response when 0.60 <= confidence < 0.85"
    )
```

### 3.3 Offline Mode Behavior

When `privacy_mode == "local_only"` or the system is operating in offline/air-gapped mode:

- **Never** invoke an ML classifier (no network calls to classification models)
- Use **deterministic keyword matching only** (see Blueprint 01, Section 8)
- If deterministic confidence < 0.60 and no ML fallback available → use `general_fast_rag`
- Log a telemetry event: `classifier_skipped_offline`

### 3.4 Decision Flowchart

```text
┌─────────────────────────────────────┐
│          User Query Received         │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│   Deterministic Keyword Matching     │
│   (pattern table from Blueprint 01)  │
└─────────────────┬───────────────────┘
                  │
                  ▼
         ┌────────────────┐
         │ confidence >= ? │
         └────────┬───────┘
                  │
        ┌─────────┼──────────────────────────┐
        │         │                          │
   >= 0.85    0.60-0.84                   < 0.60
        │         │                          │
        ▼         ▼                          ▼
   ┌─────────┐  ┌──────────────┐    ┌──────────────────┐
   │ Proceed │  │ Proceed with │    │  Offline mode?   │
   │ (clean) │  │ ambiguity    │    └────────┬─────────┘
   └─────────┘  │ note         │         yes │    no
                └──────────────┘             │     │
                                             ▼     ▼
                              ┌──────────┐  ┌────────────────────┐
                              │ Fallback │  │ ML Classifier Call  │
                              │ general_ │  └────────┬───────────┘
                              │ fast_rag │           │
                              └──────────┘           ▼
                                            ┌────────────────┐
                                            │ ML confidence  │
                                            │    >= 0.60?    │
                                            └───────┬────────┘
                                                yes │    no
                                                    │     │
                                                    ▼     ▼
                                  ┌──────────────┐  ┌───────────────────┐
                                  │ Use ML       │  │ clarification_    │
                                  │ capability   │  │ needed = true     │
                                  │ + note if    │  │ suggest up to 3   │
                                  │ < 0.85       │  │ interpretations   │
                                  └──────────────┘  └───────────────────┘
```

### 3.5 Resolution Algorithm

```python
async def resolve_ambiguity(
    query: str,
    tenant: str,
    offline_mode: bool,
    deterministic_result: ClassificationResult,
) -> AmbiguityResolution:
    """Resolve query ambiguity following threshold protocol."""

    confidence = deterministic_result.confidence
    capability_id = deterministic_result.capability_id
    alternatives = deterministic_result.alternatives[:3]

    # High confidence: proceed cleanly
    if confidence >= 0.85:
        return AmbiguityResolution(
            ambiguity_detected=False,
            confidence=confidence,
            interpreted_as=capability_id,
            alternative_interpretations=alternatives,
            clarification_needed=False,
        )

    # Medium confidence: proceed with note
    if confidence >= 0.60:
        return AmbiguityResolution(
            ambiguity_detected=True,
            confidence=confidence,
            interpreted_as=capability_id,
            alternative_interpretations=alternatives,
            clarification_needed=False,
            ambiguity_note=f"Interpreted as '{capability_id}'. "
                          f"Alternatives considered: {', '.join(alternatives)}.",
        )

    # Low confidence: try ML or fallback
    if offline_mode:
        return AmbiguityResolution(
            ambiguity_detected=True,
            confidence=0.0,
            interpreted_as="general_fast_rag",
            alternative_interpretations=alternatives,
            clarification_needed=False,
        )

    # ML classifier attempt
    ml_result = await ml_classify(query, tenant)
    if ml_result.confidence >= 0.60:
        return AmbiguityResolution(
            ambiguity_detected=True,
            confidence=ml_result.confidence,
            interpreted_as=ml_result.capability_id,
            alternative_interpretations=ml_result.alternatives[:3],
            clarification_needed=False,
            ambiguity_note=f"Interpreted as '{ml_result.capability_id}' (ML-assisted).",
        )

    # Unresolvable: request clarification
    return AmbiguityResolution(
        ambiguity_detected=True,
        confidence=ml_result.confidence,
        interpreted_as=ml_result.capability_id,
        alternative_interpretations=ml_result.alternatives[:3],
        clarification_needed=True,
        clarification_prompt=build_clarification_prompt(
            query, ml_result.alternatives[:3], tenant
        ),
    )
```

---

## 4. OperationalTraceSummary Enum Definitions

### 4.1 OmittedReason Enum

```python
from enum import Enum


class OmittedReason(str, Enum):
    """Reasons a retrieval result was omitted from the final evidence set."""

    BOILERPLATE_FILTERED = "boilerplate_filtered"
    FORBIDDEN_SOURCE = "forbidden_source"
    CROSS_MATTER_BLOCKED = "cross_matter_blocked"
    CROSS_PROFILE_BLOCKED = "cross_profile_blocked"
    WEB_BLOCKED = "web_blocked"
    LOW_RELEVANCE_SCORE = "low_relevance_score"
    DUPLICATE_VERSION = "duplicate_version"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    PERSONAL_DATA_BLOCKED = "personal_data_blocked"
```

### 4.2 Reason Detail Model

```python
class OmittedReasonDetail(BaseModel):
    """Full metadata for an omission reason."""

    code: OmittedReason
    internal_code: str = Field(description="Telemetry event code")
    message_de: str
    message_en: str
    severity: Literal["info", "warning", "security"] = "info"
```

### 4.3 Complete Mapping Table

| # | Enum Value | Internal Code | German Message | English Message | Severity |
|---|-----------|---------------|----------------|-----------------|----------|
| 1 | `boilerplate_filtered` | `OMT-001` | Standardtext herausgefiltert | Boilerplate text filtered out | info |
| 2 | `forbidden_source` | `OMT-002` | Zugriff auf diese Quelle ist nicht erlaubt | Access to this source is not permitted | security |
| 3 | `cross_matter_blocked` | `OMT-003` | Quelle aus anderem Mandat ausgeschlossen | Source from different matter excluded | security |
| 4 | `cross_profile_blocked` | `OMT-004` | Quelle aus anderem Profil ausgeschlossen | Source from different profile excluded | security |
| 5 | `web_blocked` | `OMT-005` | Webquellen sind für diese Anfrage nicht erlaubt | Web sources are not permitted for this query | warning |
| 6 | `low_relevance_score` | `OMT-006` | Ergebnis unterhalb der Relevanzschwelle | Result below relevance threshold | info |
| 7 | `duplicate_version` | `OMT-007` | Ältere Version des Dokuments übersprungen | Older document version skipped | info |
| 8 | `context_budget_exceeded` | `OMT-008` | Kontextbudget erschöpft, Ergebnis übersprungen | Context budget exceeded, result skipped | info |
| 9 | `personal_data_blocked` | `OMT-009` | Persönliche Daten gemäß Richtlinie ausgeschlossen | Personal data excluded per policy | security |

### 4.4 Registry Implementation

```python
OMITTED_REASON_REGISTRY: dict[OmittedReason, OmittedReasonDetail] = {
    OmittedReason.BOILERPLATE_FILTERED: OmittedReasonDetail(
        code=OmittedReason.BOILERPLATE_FILTERED,
        internal_code="OMT-001",
        message_de="Standardtext herausgefiltert",
        message_en="Boilerplate text filtered out",
        severity="info",
    ),
    OmittedReason.FORBIDDEN_SOURCE: OmittedReasonDetail(
        code=OmittedReason.FORBIDDEN_SOURCE,
        internal_code="OMT-002",
        message_de="Zugriff auf diese Quelle ist nicht erlaubt",
        message_en="Access to this source is not permitted",
        severity="security",
    ),
    OmittedReason.CROSS_MATTER_BLOCKED: OmittedReasonDetail(
        code=OmittedReason.CROSS_MATTER_BLOCKED,
        internal_code="OMT-003",
        message_de="Quelle aus anderem Mandat ausgeschlossen",
        message_en="Source from different matter excluded",
        severity="security",
    ),
    OmittedReason.CROSS_PROFILE_BLOCKED: OmittedReasonDetail(
        code=OmittedReason.CROSS_PROFILE_BLOCKED,
        internal_code="OMT-004",
        message_de="Quelle aus anderem Profil ausgeschlossen",
        message_en="Source from different profile excluded",
        severity="security",
    ),
    OmittedReason.WEB_BLOCKED: OmittedReasonDetail(
        code=OmittedReason.WEB_BLOCKED,
        internal_code="OMT-005",
        message_de="Webquellen sind für diese Anfrage nicht erlaubt",
        message_en="Web sources are not permitted for this query",
        severity="warning",
    ),
    OmittedReason.LOW_RELEVANCE_SCORE: OmittedReasonDetail(
        code=OmittedReason.LOW_RELEVANCE_SCORE,
        internal_code="OMT-006",
        message_de="Ergebnis unterhalb der Relevanzschwelle",
        message_en="Result below relevance threshold",
        severity="info",
    ),
    OmittedReason.DUPLICATE_VERSION: OmittedReasonDetail(
        code=OmittedReason.DUPLICATE_VERSION,
        internal_code="OMT-007",
        message_de="Ältere Version des Dokuments übersprungen",
        message_en="Older document version skipped",
        severity="info",
    ),
    OmittedReason.CONTEXT_BUDGET_EXCEEDED: OmittedReasonDetail(
        code=OmittedReason.CONTEXT_BUDGET_EXCEEDED,
        internal_code="OMT-008",
        message_de="Kontextbudget erschöpft, Ergebnis übersprungen",
        message_en="Context budget exceeded, result skipped",
        severity="info",
    ),
    OmittedReason.PERSONAL_DATA_BLOCKED: OmittedReasonDetail(
        code=OmittedReason.PERSONAL_DATA_BLOCKED,
        internal_code="OMT-009",
        message_de="Persönliche Daten gemäß Richtlinie ausgeschlossen",
        message_en="Personal data excluded per policy",
        severity="security",
    ),
}
```

### 4.5 Updated OperationalTraceSummary

```python
class OmittedResultEntry(BaseModel):
    """Single omitted result with reason and optional source hint."""

    reason: OmittedReason
    source_hint: Optional[str] = Field(
        default=None,
        description="Non-identifying hint, e.g. 'document from profile X'"
    )
    count: int = 1


class OperationalTraceSummary(BaseModel):
    """Structured operational trace for the response envelope."""

    selected_interpretation: Optional[str]
    source_scope_summary: str
    retrieval_summary: str
    evidence_cards_count: int
    validation_summary: str
    omitted_results: list[OmittedResultEntry] = Field(default_factory=list)
    total_omitted_count: int = 0
    warnings: list[str] = Field(default_factory=list)
```

---

## 5. User-Facing Error Messages for Strategy Failures

### 5.1 UserFacingMessage Model

```python
class UserFacingMessage(BaseModel):
    """Structured user-facing error/warning message with i18n support."""

    level: Literal["success", "warning", "error", "fatal"]
    title: str
    message: str
    reason_code: str = Field(description="Machine-readable failure code, e.g. 'ERR-SRC-001'")
    suggestion: Optional[str] = None
    language: str = "en"


class UserFacingMessageDef(BaseModel):
    """Definition of a user-facing message with both language variants."""

    reason_code: str
    level: Literal["success", "warning", "error", "fatal"]
    title_de: str
    title_en: str
    message_de: str
    message_en: str
    suggestion_de: Optional[str] = None
    suggestion_en: Optional[str] = None
```

### 5.2 UserFacingMessageService

```python
class UserFacingMessageService:
    """Maps internal failure codes to localized user-facing messages."""

    def __init__(self):
        self._registry: dict[str, UserFacingMessageDef] = {}
        self._load_defaults()

    def _load_defaults(self):
        for defn in USER_FACING_MESSAGE_DEFINITIONS:
            self._registry[defn.reason_code] = defn

    def get_message(
        self,
        reason_code: str,
        language: str = "en",
        **format_kwargs,
    ) -> UserFacingMessage:
        """Resolve a reason_code to a localized UserFacingMessage."""
        defn = self._registry.get(reason_code)
        if not defn:
            return UserFacingMessage(
                level="error",
                title="Unknown Error" if language == "en" else "Unbekannter Fehler",
                message=f"An unexpected error occurred (code: {reason_code}).",
                reason_code=reason_code,
                language=language,
            )

        if language == "de":
            return UserFacingMessage(
                level=defn.level,
                title=defn.title_de.format(**format_kwargs),
                message=defn.message_de.format(**format_kwargs),
                reason_code=defn.reason_code,
                suggestion=defn.suggestion_de.format(**format_kwargs) if defn.suggestion_de else None,
                language="de",
            )
        else:
            return UserFacingMessage(
                level=defn.level,
                title=defn.title_en.format(**format_kwargs),
                message=defn.message_en.format(**format_kwargs),
                reason_code=defn.reason_code,
                suggestion=defn.suggestion_en.format(**format_kwargs) if defn.suggestion_en else None,
                language="en",
            )
```

### 5.3 Failure Scenarios and Messages

| # | Scenario | Reason Code | Level |
|---|----------|------------|-------|
| 1 | Source policy blocks query | `ERR-SRC-001` | error |
| 2 | Latency budget exceeded | `ERR-LAT-001` | warning |
| 3 | Zero retrieval results | `ERR-RET-001` | warning |
| 4 | Ambiguity unresolvable | `ERR-AMB-001` | warning |
| 5 | Model unavailable | `ERR-MDL-001` | fatal |
| 6 | Citation validation failed | `ERR-CIT-001` | warning |
| 7 | Answer contract violation | `ERR-CTR-001` | error |
| 8 | Context budget fully consumed | `ERR-CTX-001` | warning |
| 9 | Authentication/authorization failure | `ERR-AUTH-001` | fatal |

### 5.4 Complete Message Definitions

```python
USER_FACING_MESSAGE_DEFINITIONS: list[UserFacingMessageDef] = [
    UserFacingMessageDef(
        reason_code="ERR-SRC-001",
        level="error",
        title_de="Quellenzugriff blockiert",
        title_en="Source Access Blocked",
        message_de="Zugriff auf externe Quellen ist für Rechtsangelegenheiten nicht erlaubt.",
        message_en="Access to external sources is not permitted for legal matters.",
        suggestion_de="Stellen Sie sicher, dass die relevanten Dokumente im aktiven Mandat hochgeladen sind.",
        suggestion_en="Ensure relevant documents are uploaded to the active matter.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-LAT-001",
        level="warning",
        title_de="Zeitbudget überschritten",
        title_en="Time Budget Exceeded",
        message_de="Die Antwort konnte nicht in der vorgesehenen Zeit erstellt werden.",
        message_en="The answer could not be generated within the time limit.",
        suggestion_de="Versuchen Sie eine spezifischere Frage oder wählen Sie den schnellen Modus.",
        suggestion_en="Try a more specific question or select fast mode.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-RET-001",
        level="warning",
        title_de="Keine Dokumente gefunden",
        title_en="No Documents Found",
        message_de="Keine relevanten Dokumente gefunden. Bitte laden Sie zunächst Dokumente hoch.",
        message_en="No relevant documents found. Please upload documents first.",
        suggestion_de="Prüfen Sie, ob das richtige Profil und Mandat aktiv ist.",
        suggestion_en="Verify the correct profile and matter are active.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-AMB-001",
        level="warning",
        title_de="Mehrdeutige Anfrage",
        title_en="Ambiguous Query",
        message_de="Die Frage konnte nicht eindeutig zugeordnet werden. Bitte formulieren Sie die Frage genauer.",
        message_en="The query could not be classified unambiguously. Please rephrase your question.",
        suggestion_de=None,
        suggestion_en=None,
    ),
    UserFacingMessageDef(
        reason_code="ERR-MDL-001",
        level="fatal",
        title_de="KI-Modell nicht verfügbar",
        title_en="AI Model Unavailable",
        message_de="Das erforderliche KI-Modell ist derzeit nicht verfügbar.",
        message_en="The required AI model is currently unavailable.",
        suggestion_de="Bitte versuchen Sie es in einigen Minuten erneut oder kontaktieren Sie den Administrator.",
        suggestion_en="Please try again in a few minutes or contact the administrator.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-CIT-001",
        level="warning",
        title_de="Quellenprüfung fehlgeschlagen",
        title_en="Citation Validation Failed",
        message_de="Einige Aussagen konnten nicht mit Quellen belegt werden.",
        message_en="Some claims could not be supported with source citations.",
        suggestion_de="Die betroffenen Aussagen sind entsprechend gekennzeichnet.",
        suggestion_en="Affected claims are labeled accordingly.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-CTR-001",
        level="error",
        title_de="Antwortformat ungültig",
        title_en="Answer Format Invalid",
        message_de="Die generierte Antwort entspricht nicht dem erwarteten Format.",
        message_en="The generated answer does not match the expected format.",
        suggestion_de="Das System versucht erneut mit strengerer Formatierung.",
        suggestion_en="The system is retrying with stricter formatting.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-CTX-001",
        level="warning",
        title_de="Kontextbudget erschöpft",
        title_en="Context Budget Exhausted",
        message_de="Das Kontextfenster ist voll. Einige Ergebnisse wurden ausgelassen.",
        message_en="The context window is full. Some results were omitted.",
        suggestion_de="Stellen Sie eine spezifischere Frage, um relevantere Ergebnisse zu erhalten.",
        suggestion_en="Ask a more specific question to get more relevant results.",
    ),
    UserFacingMessageDef(
        reason_code="ERR-AUTH-001",
        level="fatal",
        title_de="Zugriff verweigert",
        title_en="Access Denied",
        message_de="Sie haben keine Berechtigung für diese Aktion.",
        message_en="You do not have permission for this action.",
        suggestion_de="Bitte melden Sie sich erneut an oder kontaktieren Sie den Administrator.",
        suggestion_en="Please log in again or contact the administrator.",
    ),
]
```

---

## 6. Language Handling and Enforcement

### 6.1 LanguagePolicy Model

```python
class LanguagePolicy(BaseModel):
    """Language handling policy for a tenant/profile."""

    primary_languages: list[str] = Field(
        description="ISO 639-1 codes of primary response languages, ordered by preference"
    )
    supported_languages: list[str] = Field(
        description="All languages the system can respond in"
    )
    fallback_language: str = Field(
        description="Language to use when detected language is unsupported"
    )
    terminology_preservation: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Language → list of terms that must be preserved verbatim regardless of response language"
    )
    detect_query_language: bool = True
    enforce_response_language: bool = True
    max_language_retry: int = 1
```

### 6.2 Language Detection

```python
import re
from typing import Optional


# Character range heuristics for fast detection
LANGUAGE_CHAR_PATTERNS: dict[str, re.Pattern] = {
    "de": re.compile(r"[äöüßÄÖÜ]"),
    "fr": re.compile(r"[éèêëàâùûôîçÉÈÊ]"),
    "es": re.compile(r"[ñáéíóúüÑ¿¡]"),
    "zh": re.compile(r"[\u4e00-\u9fff]"),
    "ja": re.compile(r"[\u3040-\u309f\u30a0-\u30ff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"),
    "ar": re.compile(r"[\u0600-\u06ff]"),
}

# Common word heuristics (first 50 chars)
LANGUAGE_WORD_PATTERNS: dict[str, list[str]] = {
    "de": ["der", "die", "das", "ist", "und", "für", "nicht", "ich", "ein", "was"],
    "en": ["the", "is", "are", "what", "how", "can", "this", "that", "from", "with"],
    "fr": ["le", "la", "les", "est", "une", "des", "que", "dans", "pour", "pas"],
}


def detect_language(text: str, policy: LanguagePolicy) -> str:
    """
    Detect query language using character analysis + word frequency heuristic.
    
    Strategy:
      1. Check first 50 characters for distinctive character patterns
      2. Tokenize and check against common word lists
      3. If no match, return primary_languages[0]
    
    Returns:
        ISO 639-1 language code
    """
    sample = text[:50].lower()

    # Step 1: Character-based detection (high confidence for non-Latin scripts)
    for lang, pattern in LANGUAGE_CHAR_PATTERNS.items():
        if pattern.search(sample):
            if lang in policy.supported_languages:
                return lang
            # Special case: German characters detected
            if lang == "de":
                return "de"

    # Step 2: Word-frequency heuristic
    words = re.findall(r"\b\w+\b", sample)
    scores: dict[str, int] = {}
    for lang, keywords in LANGUAGE_WORD_PATTERNS.items():
        scores[lang] = sum(1 for w in words if w in keywords)

    if scores:
        best_lang = max(scores, key=scores.get)
        if scores[best_lang] >= 2:
            return best_lang

    # Step 3: Fallback to primary language
    return policy.primary_languages[0]
```

### 6.3 Output Language Enforcement

The synthesis node enforces response language through three mechanisms:

```python
async def enforce_language(
    response_text: str,
    expected_language: str,
    answer_contract: AnswerContract,
    policy: LanguagePolicy,
    llm_client,
) -> tuple[str, bool]:
    """
    Validate and enforce response language.
    
    Returns:
        (final_text, was_retried)
    """
    # Check if response matches expected language
    detected_response_lang = detect_language(response_text, policy)

    if detected_response_lang == expected_language:
        return response_text, False

    if not policy.enforce_response_language:
        return response_text, False

    # Language mismatch: re-prompt with explicit instruction (max 1 retry)
    retry_prompt = (
        f"CRITICAL: Your previous response was in '{detected_response_lang}' "
        f"but MUST be in '{expected_language}'. "
        f"Rewrite the entire response in '{expected_language}'. "
        f"Preserve all technical terms and citations exactly as they are."
    )

    corrected = await llm_client.generate(
        system_prompt=retry_prompt,
        user_prompt=response_text,
        max_tokens=answer_contract.max_length_tokens or 2000,
    )

    return corrected, True
```

### 6.4 Terminology Preservation

When queries or responses contain terms from the `terminology_preservation` list, those terms are preserved verbatim regardless of the detected or target language:

```python
def preserve_terminology(
    text: str,
    policy: LanguagePolicy,
    target_language: str,
) -> list[str]:
    """
    Extract terms that must be preserved verbatim in the response.
    
    These are injected into the synthesis prompt as:
    "The following terms MUST appear exactly as written: [...]"
    """
    preserved = []
    for lang, terms in policy.terminology_preservation.items():
        for term in terms:
            if term.lower() in text.lower():
                preserved.append(term)
    return preserved
```

### 6.5 Synthesis Prompt Integration

```text
Language instruction added to synthesis prompt:

---
LANGUAGE: Respond entirely in {answer_contract.language}.
PRESERVED TERMS: The following terms must appear exactly as written,
regardless of response language: {preserved_terms}
---
```

### 6.6 Language Mismatch Handling

| Scenario | Behavior |
|----------|----------|
| Query language matches contract language | Normal processing |
| Query language differs but is in `supported_languages` | Respond in contract language, no note |
| Query language not in `supported_languages` | Respond in `fallback_language`, add note explaining language limitation |
| Response language mismatches contract | Re-prompt once; if still wrong, return with `ERR-LANG-001` warning |

### 6.7 LanguagePolicy YAML Examples

#### Quellex (German primary)

```yaml
# tenant_language_policies/quellex.yaml
primary_languages:
  - de
supported_languages:
  - de
  - en
fallback_language: de
detect_query_language: true
enforce_response_language: true
max_language_retry: 1
terminology_preservation:
  de:
    - Sachverständiger
    - Gutachten
    - Beweisbeschluss
    - Streitwert
    - Klageerwiderung
    - Berufung
    - Beweiswürdigung
    - Vergleichsvorschlag
    - Schriftsatz
    - Akteneinsicht
    - Sachverhalt
    - Rechtsfolge
    - Beweislast
  en:
    - GDPR
    - API
    - OAuth
```

#### RecallHub (English primary)

```yaml
# tenant_language_policies/recallhub.yaml
primary_languages:
  - en
supported_languages:
  - en
  - de
  - fr
fallback_language: en
detect_query_language: true
enforce_response_language: true
max_language_retry: 1
terminology_preservation:
  en:
    - ROI
    - KPI
    - OKR
    - SaaS
    - API
  de:
    - Datenschutz
    - Geschäftsführer
    - Betriebsrat
```

### 6.8 Code-Mixed Query Handling

When a query contains terms from multiple languages (common in legal/business contexts):

```python
def handle_code_mixed_query(
    query: str,
    detected_language: str,
    policy: LanguagePolicy,
) -> str:
    """
    Determine effective response language for code-mixed queries.
    
    Rules:
      1. If majority language matches a primary_language → use it
      2. If preserved terms are detected → use the language they belong to
      3. Otherwise → use detected_language if supported, else fallback
    """
    preserved = preserve_terminology(query, policy, detected_language)

    # If preserved terms found and they belong to a primary language,
    # that primary language takes precedence
    for lang in policy.primary_languages:
        lang_terms = policy.terminology_preservation.get(lang, [])
        if any(t in preserved for t in lang_terms):
            return lang

    if detected_language in policy.supported_languages:
        return detected_language

    return policy.fallback_language
```

---

## Appendix A: Cross-Reference to Resolved Gaps

| Gap ID | Description | Resolved In |
|--------|-------------|-------------|
| B1-1 | Source policy resolution semantics undefined | Section 1 (intersection semantics, algorithm) |
| B1-2 | AnswerContract versioning and resolution undefined | Section 2 (version resolution, DB storage) |
| B1-3 | Ambiguity handling thresholds and offline behavior undefined | Section 3 (thresholds, offline fallback) |
| B1-4 | OmittedReason enum values undefined | Section 4 (full enum, i18n mapping) |
| UX-3 | User-facing error messages not specified | Section 5 (full message service, i18n) |
| UX-4 | Language enforcement and terminology preservation undefined | Section 6 (detection, enforcement, retry) |

---

## Appendix B: Integration Points with Existing Code

| Component | File | Integration |
|-----------|------|-------------|
| FederatedSearch | `backend/agent/federated_search.py` | Receives `SearchRequest` with policy fields; enforces Layer 1 |
| BusinessContextResolver | `backend/services/business_context_resolver.py` | Produces `ResolvedSourcePolicy` and `AnswerContract` |
| Coordinator | `backend/agent/coordinator.py` | Passes `BusinessContext` to strategy runner |
| Strategy Base | `backend/agent/strategies/base.py` | Consumes `AnswerContract` for synthesis formatting |
| Telemetry | `backend/models/telemetry.py` | Records `OmittedReason` codes and `UserFacingMessage` events |
| Response Envelope | `backend/models/business_context.py` | Includes `AmbiguityResolution` and `OperationalTraceSummary` |
| Profile Config | `backend/profiles.yaml` | Provides profile-level `SourcePolicy` overrides |
| Tenant Config | `backend/config/tenant_strategy_policies/*.yaml` | Provides tenant-level defaults |

---

## Appendix C: Migration Notes

1. **Existing `SourcePolicy` model in Blueprint 01** is superseded by the expanded model in Section 1. The new model adds `Optional` semantics for layered resolution.
2. **Existing `AnswerContract` model in Blueprint 01** is superseded by Section 2. The new model adds `OutputSection` structure, `TableSchema`, and versioning.
3. **Existing `OperationalTraceSummary` in Blueprint 01** is extended with typed `OmittedResultEntry` replacing the untyped `list[str]`.
4. **New collections required:** `answer_contracts` (indexed on `format_id` + `version`).
5. **New config files required:** `backend/config/tenant_language_policies/{tenant}.yaml`.
