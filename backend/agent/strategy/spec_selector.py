"""Strategy spec selection based on capability, mode, and tenant policy."""
from __future__ import annotations

import logging
import time
from typing import Optional

from backend.agent.strategy.models import BusinessContext, StrategySpec

logger = logging.getLogger(__name__)


class StrategySpecSelector:
    """
    Selects the best strategy spec for a given request context.

    Selection criteria (in priority order):
    1. capability_id matches spec's target capabilities
    2. Privacy compliance (local_only constraint from tenant)
    3. Agent mode (fast -> low-latency specs, deep -> quality specs)
    4. Status is not "draft"
    5. Rank: profile_key match > domain match > generic
    6. Tie-break: strategy_id ascending (deterministic)

    Fallback: returns None -> coordinator uses legacy path.

    Spec source: in-memory cache with 5-minute TTL, backed by MongoDB.
    """

    def __init__(self, db=None, cache_ttl_seconds: int = 300):
        self.db = db
        self._cache: list[StrategySpec] = []
        self._cache_loaded_at: float = 0.0
        self._cache_ttl = cache_ttl_seconds

    async def select(
        self,
        capability_id: Optional[str],
        agent_mode: str = "auto",
        tenant_id: str = "recallhub",
        privacy_mode: str = "standard",
        business_context: Optional[BusinessContext] = None,
    ) -> Optional[StrategySpec]:
        """
        Select the best matching strategy spec.

        Args:
            capability_id: Detected capability (e.g., "legal_hearing_questions")
            agent_mode: Execution speed preference ("auto", "fast", "deep")
            tenant_id: Current tenant
            privacy_mode: Privacy constraint ("local_only" restricts to local models)
            business_context: Full resolved context for additional filtering

        Returns:
            Best matching StrategySpec, or None for legacy fallback
        """
        specs = await self._load_specs()
        if not specs:
            return None

        # Apply filters
        candidates = self._filter_specs(specs, capability_id, agent_mode, privacy_mode)
        if not candidates:
            logger.debug(
                f"No specs match filters: capability={capability_id}, "
                f"mode={agent_mode}, privacy={privacy_mode}"
            )
            return None

        # Rank remaining candidates
        ranked = self._rank_specs(candidates, capability_id, tenant_id, business_context)
        if not ranked:
            return None

        selected = ranked[0]
        logger.info(
            f"Selected strategy spec: {selected.strategy_id} "
            f"(capability={capability_id}, mode={agent_mode})"
        )
        return selected

    async def _load_specs(self) -> list[StrategySpec]:
        """Load all active specs from DB (with cache)."""
        if self._is_cache_valid():
            return self._cache

        if self.db is None:
            self._cache = []
            self._cache_loaded_at = time.time()
            return self._cache

        try:
            collection = self.db.get_collection("strategy_specs")
            cursor = collection.find({"status": {"$ne": "archived"}})
            raw_docs = await cursor.to_list(length=500)
            self._cache = [StrategySpec(**doc) for doc in raw_docs if doc]
            self._cache_loaded_at = time.time()
            logger.debug(f"Loaded {len(self._cache)} strategy specs from DB")
        except Exception as e:
            logger.warning(f"Failed to load strategy specs from DB: {e}")
            # Keep stale cache if available
            if not self._cache:
                self._cache = []
            self._cache_loaded_at = time.time()

        return self._cache

    def _filter_specs(
        self,
        specs: list[StrategySpec],
        capability_id: Optional[str],
        agent_mode: str,
        privacy_mode: str,
    ) -> list[StrategySpec]:
        """Apply all filter criteria to narrow candidates."""
        filtered: list[StrategySpec] = []

        for spec in specs:
            # Filter out drafts
            if spec.status == "draft":
                continue

            # Privacy compliance: if local_only required, spec must support it
            if privacy_mode == "local_only" and not spec.budgets.local_only:
                continue

            # Agent mode filtering
            if agent_mode == "fast":
                # Prefer specs with tight latency targets
                if spec.budgets.latency_target_ms > 5000:
                    continue
            elif agent_mode == "deep":
                # For deep mode, skip specs with very tight latency (< 3000ms)
                # as they likely sacrifice quality for speed
                if spec.budgets.latency_target_ms < 3000:
                    continue

            # Capability matching (soft filter — allow generic specs through)
            # This is handled in ranking, not hard-filtered here
            filtered.append(spec)

        return filtered

    def _rank_specs(
        self,
        specs: list[StrategySpec],
        capability_id: Optional[str],
        tenant_id: str,
        business_context: Optional[BusinessContext],
    ) -> list[StrategySpec]:
        """Rank remaining candidates by relevance."""

        def _score(spec: StrategySpec) -> tuple[int, str]:
            """Return (priority_score, strategy_id) for sorting."""
            priority = 0

            # Exact capability match (highest priority)
            if capability_id:
                if spec.capability_id == capability_id:
                    priority += 100
                elif capability_id in spec.tags:
                    priority += 80

            # Profile key match from business context
            if business_context and business_context.profile_key:
                if spec.tenant_scope == business_context.profile_key:
                    priority += 50
                elif business_context.profile_key in spec.tags:
                    priority += 30

            # Tenant/domain match
            if spec.tenant_scope == tenant_id:
                priority += 50
            elif spec.tenant_scope == "default":
                priority += 10

            # Active status bonus over candidate
            if spec.status == "active":
                priority += 5
            elif spec.status == "candidate":
                priority += 2

            # Negative priority for ascending strategy_id tie-break
            return (priority, spec.strategy_id)

        # Sort by priority descending, then strategy_id ascending for tie-break
        ranked = sorted(
            specs,
            key=lambda s: (-_score(s)[0], _score(s)[1]),
        )
        return ranked

    def _is_cache_valid(self) -> bool:
        """Check if in-memory cache is still fresh."""
        if self._cache_loaded_at == 0.0:
            return False
        elapsed = time.time() - self._cache_loaded_at
        return elapsed < self._cache_ttl

    def invalidate_cache(self) -> None:
        """Force cache refresh on next select() call."""
        self._cache_loaded_at = 0.0
        self._cache = []
