# Blueprint 11 — Security, Privacy, and Tenant Governance

## 1. Objective

Ensure that strategy experimentation does not create privacy, legal, or tenant-boundary risks.

This is critical because the proposed scheduler will automatically run many prompts and strategies, potentially overnight, over sensitive documents.

---

## 2. Core principles

1. **No strategy may weaken tenant/profile/matter access control.**
2. **Private legal/matter prompts default to no web and no cross-matter search.**
3. **Experimentation must respect the same source policy as production.**
4. **Raw telemetry must remain restricted and short-lived.**
5. **Promotion requires privacy-policy pass, not only quality score.**
6. **LLM judges must not receive raw private content unless policy permits it.**

---

## 3. Strategy safety validation

Before activating or running a strategy candidate, validate:

```python
class StrategySafetyResult(BaseModel):
    passed: bool
    fatal_issues: list[str]
    warnings: list[str]
    policy_snapshot: dict
```

Fatal issues:

- `allow_web=true` for private legal matter without explicit override
- `allow_cross_matter=true` for Quellex legal matter strategy
- citations disabled where answer contract requires citations
- raw telemetry forced in production mode
- judge model is cloud but privacy mode is `local_only`
- source scope includes unauthorized profile/cloud/personal source

---

## 4. Tenant strategy policies

Create:

```text
backend/config/tenant_strategy_policies/quellex.yaml
backend/config/tenant_strategy_policies/recallhub.yaml
```

### Quellex policy

```yaml
default_privacy_mode: local_only
legal_matter_defaults:
  allow_web: false
  allow_cross_matter: false
  allow_cross_profile: false
  require_source_spans: true
  require_citation_coverage: true
  min_citation_coverage: 0.85
  raw_telemetry_allowed: dev_only
  cloud_judge_allowed: false unless admin_override
promotion_requires_manual_approval: true
```

### RecallHub policy

```yaml
default_privacy_mode: hybrid
business_research_defaults:
  allow_web: true when prompt asks for current/external facts
  allow_cross_profile: only if user has access
  require_internal_claim_citations: true
  raw_telemetry_allowed: dev_beta_only
promotion_requires_manual_approval: true
```

---

## 5. Experiment data handling

### Protected telemetry

Safe for general analytics and reports.

### Raw telemetry

Only allowed in:

- development
- trusted beta debugging
- explicitly enabled local admin mode

Never:

- committed to Git
- exported in reports by default
- passed to cloud judge in `local_only` mode

---

## 6. LLM judge privacy

Judge execution must use same privacy policy.

```text
if privacy_mode == local_only:
    judge_model must be local OR judge disabled
if privacy_mode == hybrid:
    cloud judge allowed only for pseudonymized evidence unless admin override
if privacy_mode == cloud_allowed:
    cloud judge allowed subject to tenant policy
```

---

## 7. Source-boundary enforcement

Do not rely only on prompts.

Enforce source boundaries in retrieval layer:

```python
SearchRequest(
  allowed_profile_ids=[...],
  allowed_matter_ids=[...],
  excluded_matter_ids=[...],
  allow_personal=False,
  allow_web=False,
)
```

The LLM must not decide whether cross-matter search is allowed.

---

## 8. Operational trace safety

Do not expose internal reasoning.

Expose:

- strategy ID
- source scope summary
- retrieval counts
- evidence card count
- validation status
- warnings

Do not expose:

- chain-of-thought
- full planning text
- hidden judge rationale if it contains sensitive data
- raw prompts in protected mode

---

## 9. Promotion governance

A strategy can become default only after:

- deterministic validators pass
- privacy validator passes
- source scope validator passes
- citation validator passes for legal strategies
- score threshold met
- latency threshold met
- manual approval by admin or configured owner

Promotion record:

```javascript
{
  "strategy_id": "...",
  "from_status": "candidate",
  "to_status": "default",
  "approved_by": "user_id",
  "approval_notes": "...",
  "evaluation_run_id": "...",
  "policy_result": {},
  "created_at": ISODate
}
```

---

## 10. Acceptance criteria

- Unsafe strategy specs fail validation before execution.
- Source policy is enforced in retrieval, not only in prompts.
- Local-only mode disables cloud judges and cloud LLM calls.
- Raw telemetry cannot be enabled accidentally in production.
- Promotion endpoint checks safety and requires authorization.
