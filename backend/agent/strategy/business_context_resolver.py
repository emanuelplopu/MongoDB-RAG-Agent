"""
Business Context Resolver for Strategy OS Phase 1.

Resolves tenant policies, detects business capabilities, and produces
a BusinessContext envelope that guides strategy selection and source enforcement.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from backend.agent.strategy.models import (
    AmbiguityResolution,
    AnswerContract,
    BusinessContext,
    ColumnDef,
    LanguagePolicy,
    OutputSection,
    ResolvedSourcePolicy,
    SourcePolicy,
    TableSchema,
)

logger = logging.getLogger(__name__)


class BusinessContextResolver:
    """
    Resolves business context from tenant policies, profile config, and query analysis.

    Pipeline:
    1. Load tenant policy from YAML
    2. Detect business capability from query (deterministic keyword matching)
    3. Resolve source policy via intersection (tenant ∩ profile ∩ strategy)
    4. Resolve answer contract for detected capability
    5. Return BusinessContext envelope
    """

    def __init__(self, tenant_id: str, config_path: str = "backend/config"):
        self.tenant_id = tenant_id
        self.config_path = Path(config_path)
        self._tenant_policy: Optional[dict] = None
        self._capabilities: list[dict] = []
        self._answer_contracts: dict[str, dict] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load configuration files on first use."""
        if self._loaded:
            return
        self._load_tenant_policy()
        self._load_capabilities()
        self._load_answer_contracts()
        self._loaded = True

    def _load_tenant_policy(self) -> None:
        """Load tenant-specific strategy policy from YAML."""
        policy_path = self.config_path / "tenant_strategy_policies" / f"{self.tenant_id}.yaml"
        if not policy_path.exists():
            # Fall back to default
            policy_path = self.config_path / "tenant_strategy_policies" / "default.yaml"

        if policy_path.exists():
            with open(policy_path, "r", encoding="utf-8") as f:
                self._tenant_policy = yaml.safe_load(f) or {}
            logger.debug(f"Loaded tenant policy: {policy_path}")
        else:
            self._tenant_policy = {}
            logger.warning(f"No tenant policy found for '{self.tenant_id}', using empty defaults")

    def _load_capabilities(self) -> None:
        """Load capability definitions for this tenant."""
        caps_dir = self.config_path / "capabilities"
        if not caps_dir.exists():
            logger.warning(f"Capabilities directory not found: {caps_dir}")
            return

        for yaml_file in caps_dir.glob("*.yaml"):
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            tenant_scope = data.get("tenant_scope", "default")
            # Load capabilities matching this tenant or "default"
            if tenant_scope in (self.tenant_id, "default", "recallhub"):
                caps = data.get("capabilities", [])
                self._capabilities.extend(caps)

        logger.debug(f"Loaded {len(self._capabilities)} capabilities for tenant '{self.tenant_id}'")

    def _load_answer_contracts(self) -> None:
        """Load answer contract definitions from YAML."""
        contracts_dir = self.config_path / "answer_contracts"
        if not contracts_dir.exists():
            logger.warning(f"Answer contracts directory not found: {contracts_dir}")
            return

        for yaml_file in contracts_dir.glob("*.yaml"):
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            format_id = data.get("format_id")
            if format_id:
                self._answer_contracts[format_id] = data

        logger.debug(f"Loaded {len(self._answer_contracts)} answer contracts")

    async def resolve(
        self,
        query: str,
        profile_key: Optional[str] = None,
        matter_id: Optional[str] = None,
        accessible_profiles: Optional[list[str]] = None,
        strategy_source_policy: Optional[SourcePolicy] = None,
    ) -> BusinessContext:
        """
        Resolve full business context for a query.

        Args:
            query: The user's query text
            profile_key: Active profile key (e.g., "test-law")
            matter_id: Active matter ID if applicable
            accessible_profiles: List of profile keys the user can access
            strategy_source_policy: Optional source policy from selected strategy spec

        Returns:
            BusinessContext with resolved policies, capability, and contract
        """
        self._ensure_loaded()

        # Step 1: Detect capability
        capability_id, confidence = self._detect_capability(query)

        # Step 2: Build ambiguity resolution
        ambiguity = self._build_ambiguity(capability_id, confidence)

        # Step 3: Resolve source policy (intersection of layers)
        resolved_policy = self._resolve_source_policy(
            profile_key=profile_key,
            matter_id=matter_id,
            accessible_profiles=accessible_profiles or [],
            strategy_policy=strategy_source_policy,
        )

        # Step 4: Resolve answer contract
        answer_contract = self._resolve_answer_contract(capability_id)

        # Step 5: Get language policy
        language_policy = self._get_language_policy()

        return BusinessContext(
            capability_id=capability_id,
            capability_confidence=confidence,
            resolved_source_policy=resolved_policy,
            answer_contract=answer_contract,
            ambiguity=ambiguity,
            language_policy=language_policy,
            profile_key=profile_key,
            matter_id=matter_id,
            tenant_id=self.tenant_id,
        )

    def _detect_capability(self, query: str) -> tuple[str, float]:
        """
        Detect business capability from query using deterministic keyword/pattern matching.

        Returns:
            Tuple of (capability_id, confidence_score)
        """
        query_lower = query.lower().strip()
        best_match: Optional[str] = None
        best_score: float = 0.0

        for cap in self._capabilities:
            cap_id = cap.get("capability_id", "")
            keywords = cap.get("detection_keywords", [])
            patterns = cap.get("detection_patterns", [])

            score = 0.0

            # Keyword matching (each keyword hit adds to score)
            keyword_hits = sum(1 for kw in keywords if kw.lower() in query_lower)
            if keywords:
                score = keyword_hits / len(keywords)

            # Pattern matching (any pattern match boosts significantly)
            for pattern in patterns:
                try:
                    if re.search(pattern, query, re.IGNORECASE):
                        score = max(score, 0.85)  # Pattern match = high confidence
                        break
                except re.error:
                    logger.warning(f"Invalid regex pattern for capability '{cap_id}': {pattern}")

            # Boost if multiple keyword hits
            if keyword_hits >= 2:
                score = min(score + 0.2, 1.0)

            if score > best_score:
                best_score = score
                best_match = cap_id

        # Apply confidence thresholds
        if best_score >= 0.85:
            return (best_match or "general_fast_rag", best_score)
        elif best_score >= 0.60:
            return (best_match or "general_fast_rag", best_score)
        else:
            # Fallback to tenant default or general
            default_cap = (
                self._tenant_policy.get("default_capability", "general_fast_rag")
                if self._tenant_policy
                else "general_fast_rag"
            )
            return (default_cap, best_score if best_score > 0 else 0.0)

    def _build_ambiguity(self, capability_id: str, confidence: float) -> AmbiguityResolution:
        """Build ambiguity resolution metadata from detection results."""
        if confidence >= 0.85:
            return AmbiguityResolution(
                ambiguity_detected=False,
                confidence=confidence,
                interpreted_as=capability_id,
            )
        elif confidence >= 0.60:
            return AmbiguityResolution(
                ambiguity_detected=True,
                confidence=confidence,
                interpreted_as=capability_id,
                alternative_interpretations=self._get_alternative_capabilities(capability_id),
            )
        else:
            return AmbiguityResolution(
                ambiguity_detected=True,
                confidence=confidence,
                interpreted_as=capability_id,
                clarification_needed=confidence < 0.30,
                alternative_interpretations=self._get_alternative_capabilities(capability_id),
            )

    def _get_alternative_capabilities(self, exclude_id: str) -> list[str]:
        """Get alternative capability IDs (excluding the detected one)."""
        alternatives = [
            cap.get("capability_id", "")
            for cap in self._capabilities
            if cap.get("capability_id") != exclude_id and cap.get("detection_keywords")
        ]
        return alternatives[:3]  # Max 3 alternatives

    def _resolve_source_policy(
        self,
        profile_key: Optional[str],
        matter_id: Optional[str],
        accessible_profiles: list[str],
        strategy_policy: Optional[SourcePolicy] = None,
    ) -> ResolvedSourcePolicy:
        """
        Resolve source policy via intersection semantics.

        Each layer can only RESTRICT (never expand) permissions.
        Order: tenant_policy ∩ profile_policy ∩ strategy_policy
        """
        # Layer 1: Tenant defaults
        tenant_sp = self._tenant_policy.get("source_policy", {}) if self._tenant_policy else {}

        # Start with tenant values (most permissive layer)
        allow_cross_profile = tenant_sp.get("allow_cross_profile", False)
        allow_cross_matter = tenant_sp.get("allow_cross_matter", False)
        allow_web = tenant_sp.get("allow_web", False)
        allow_personal = tenant_sp.get("allow_personal", False)
        allow_cloud_private = tenant_sp.get("allow_cloud_private", False)
        web_requires_sanitization = tenant_sp.get("web_requires_sanitization", True)
        require_source_spans = tenant_sp.get("require_source_spans", False)
        prefer_latest_versions = tenant_sp.get("prefer_latest_versions", True)
        exclude_boilerplate = tenant_sp.get("exclude_boilerplate", False)
        excluded_sources: list[str] = list(tenant_sp.get("excluded_sources", []))

        resolved_from = ["tenant"]

        # Layer 2: Strategy policy (can only restrict further)
        if strategy_policy:
            resolved_from.append("strategy")
            allow_cross_profile = allow_cross_profile and strategy_policy.allow_cross_profile
            allow_cross_matter = allow_cross_matter and strategy_policy.allow_cross_matter
            allow_web = allow_web and strategy_policy.allow_web
            allow_personal = allow_personal and strategy_policy.allow_personal
            allow_cloud_private = allow_cloud_private and strategy_policy.allow_cloud_private
            # For restrictive flags, take the more restrictive (True)
            require_source_spans = require_source_spans or strategy_policy.require_source_spans
            exclude_boilerplate = exclude_boilerplate or strategy_policy.exclude_boilerplate
            # Merge excluded sources
            excluded_sources = list(set(excluded_sources + strategy_policy.excluded_sources))

        # Determine allowed profiles
        allowed_profile_ids = accessible_profiles if accessible_profiles else []
        if profile_key and profile_key not in allowed_profile_ids:
            allowed_profile_ids = [profile_key] + allowed_profile_ids

        # If cross-profile not allowed, restrict to current profile only
        if not allow_cross_profile and profile_key:
            allowed_profile_ids = [profile_key]

        # Determine allowed matters
        allowed_matter_ids: list[str] = []
        excluded_matter_ids: list[str] = []
        if matter_id:
            if not allow_cross_matter:
                allowed_matter_ids = [matter_id]

        return ResolvedSourcePolicy(
            allow_cross_profile=allow_cross_profile,
            allow_cross_matter=allow_cross_matter,
            allow_web=allow_web,
            allow_personal=allow_personal,
            allow_cloud_private=allow_cloud_private,
            web_requires_sanitization=web_requires_sanitization,
            require_source_spans=require_source_spans,
            prefer_latest_versions=prefer_latest_versions,
            exclude_boilerplate=exclude_boilerplate,
            allowed_profile_ids=allowed_profile_ids,
            allowed_matter_ids=allowed_matter_ids,
            excluded_matter_ids=excluded_matter_ids,
            primary_sources=tenant_sp.get("primary_sources", ["documents"]),
            secondary_sources=tenant_sp.get("secondary_sources", []),
            excluded_sources=excluded_sources,
            resolved_from=resolved_from,
        )

    def _resolve_answer_contract(self, capability_id: str) -> Optional[AnswerContract]:
        """
        Resolve answer contract for detected capability.

        Resolution: capability -> default_answer_contract_id -> load from YAML
        """
        # Find capability definition
        cap_def = None
        for cap in self._capabilities:
            if cap.get("capability_id") == capability_id:
                cap_def = cap
                break

        if not cap_def:
            return None

        contract_id = cap_def.get("default_answer_contract_id")
        if not contract_id:
            return None

        # Load from YAML cache
        contract_data = self._answer_contracts.get(contract_id)
        if not contract_data:
            logger.debug(f"Answer contract '{contract_id}' not found in YAML files")
            return None

        try:
            # Parse output sections
            sections = []
            for sec_data in contract_data.get("output_sections", []):
                sections.append(OutputSection(**sec_data))

            # Parse table schema if present
            table_schema = None
            ts_data = contract_data.get("table_schema")
            if ts_data:
                columns = [ColumnDef(**col) for col in ts_data.get("columns", [])]
                table_schema = TableSchema(
                    columns=columns,
                    row_source=ts_data.get("row_source", ""),
                    sort_by=ts_data.get("sort_by"),
                    min_rows=ts_data.get("min_rows"),
                    max_rows=ts_data.get("max_rows"),
                )

            return AnswerContract(
                format_id=contract_data.get("format_id", contract_id),
                version=contract_data.get("version", 1),
                language=contract_data.get("language", "de"),
                output_sections=sections,
                table_schema=table_schema,
                tone=contract_data.get("tone", "professional"),
                citation_granularity=contract_data.get("citation_granularity", "chunk"),
                unsupported_claim_policy=contract_data.get("unsupported_claim_policy", "flag"),
                required_fields=contract_data.get("required_fields", []),
                max_length_tokens=contract_data.get("max_length_tokens"),
            )
        except Exception as e:
            logger.warning(f"Failed to parse answer contract '{contract_id}': {e}")
            return None

    def _get_language_policy(self) -> Optional[LanguagePolicy]:
        """Get language policy from tenant config."""
        if not self._tenant_policy:
            return None

        lp_data = self._tenant_policy.get("language_policy")
        if not lp_data:
            return None

        try:
            return LanguagePolicy(**lp_data)
        except Exception as e:
            logger.warning(f"Failed to parse language policy: {e}")
            return None
