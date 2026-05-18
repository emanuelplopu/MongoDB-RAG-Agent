"""Strategy spec selection backed by the persisted ``strategy_specs`` store.

Phase 5 / Task 65 deliverable. The selector is responsible for resolving
the best :class:`StrategySpec` for a given request context. It is a thin
in-memory cache layered on top of a :class:`SpecStore` (typically
:class:`MongoSpecStore`) and falls back to the
:class:`LegacyStrategyAdapter` spec when no active spec exists for the
requested capability.

Caching contract
----------------
The cache is keyed by ``(capability_id, tenant_id)`` and stamped with a
TTL:

* ``cache_ttl_seconds`` (default 300s) for entries resolved from the
  store.
* ``fallback_ttl_seconds`` (default 60s) for entries served from the
  legacy adapter; the shorter window ensures a freshly promoted spec
  appears quickly after operator action.

Selection ranking
-----------------
When the underlying store returns multiple ``status='active'`` specs for
the same ``capability_id``, the selector deterministically picks one:

1. Exact ``tenant_scope == tenant_id`` match wins.
2. Otherwise the most recent ``updated_at`` (then ``created_at``) wins.
3. Final tie-break: ``strategy_id`` ascending.

Privacy / agent-mode filters
----------------------------
Privacy (``privacy_mode='local_only'``) and agent-mode (``fast``/``deep``)
constraints are still applied as a post-list filter to preserve the
public selector API used by callers in
:mod:`backend.agent.coordinator` and the strategy CLI.

The selector does **not** mutate the store. Cache invalidation is
exposed via :meth:`StrategySpecSelector.clear_cache` so promotion /
rollback flows can drop stale entries on demand.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.agent.strategy.adaptive_decision_store import (
    AdaptiveDecisionDoc,
    AdaptiveDecisionStore,
)
from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter
from backend.agent.strategy.models import BusinessContext, StrategySpec
from backend.agent.strategy.operational_trace import emit_trace_event
from backend.agent.strategy.spec_store import (
    InMemorySpecStore,
    MongoSpecStore,
    SpecStore,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "FALLBACK_CACHE_TTL_SECONDS",
    "FAST_PATH_ELIGIBLE_CAPABILITIES",
    "AdaptiveSelector",
    "FastPathRules",
    "NoActiveSpecError",
    "StrategySpecSelector",
]

#: Capabilities for which the fast-path bypass is allowed when the
#: business context does not yet expose an explicit
#: ``answer_contract.has_template`` flag. Documented as the hardcoded
#: fallback list per Blueprint 08 §9 (see :class:`FastPathRules`).
FAST_PATH_ELIGIBLE_CAPABILITIES: frozenset[str] = frozenset(
    {"legal_hearing_questions", "business_summary"}
)

#: Default TTL applied to cache entries served from the underlying store.
DEFAULT_CACHE_TTL_SECONDS: int = 300
#: Default TTL applied when serving a cached entry from the legacy fallback.
FALLBACK_CACHE_TTL_SECONDS: int = 60


class NoActiveSpecError(LookupError):
    """Raised when neither the spec store nor the legacy adapter can satisfy a request.

    Attributes:
        capability_id: The requested capability id.
        tenant_id: The tenant scope that was attempted.
    """

    def __init__(self, capability_id: Optional[str], tenant_id: Optional[str]) -> None:
        self.capability_id = capability_id
        self.tenant_id = tenant_id
        super().__init__(
            f"No active strategy spec available for capability_id={capability_id!r} "
            f"tenant_id={tenant_id!r} and legacy adapter declined to provide one"
        )


class _CacheEntry:
    """Internal cache entry holding a resolved spec plus its expiry stamp."""

    __slots__ = ("spec", "expires_at", "from_fallback")

    def __init__(self, spec: StrategySpec, expires_at: float, from_fallback: bool) -> None:
        self.spec = spec
        self.expires_at = expires_at
        self.from_fallback = from_fallback

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at


class StrategySpecSelector:
    """Resolve the active :class:`StrategySpec` for a capability/tenant pair.

    The selector is intentionally backend-agnostic: callers inject a
    :class:`SpecStore` (production code passes the shared
    :class:`MongoSpecStore` from ``app.state.spec_store``; tests pass an
    :class:`InMemorySpecStore`). When no store is provided the selector
    behaves as a legacy-adapter-only resolver, which keeps the CLI
    smoke-test path and unit tests functional without a database.

    Args:
        store: Persisted spec store. ``None`` disables DB lookups and
            forces every call to consult the legacy adapter.
        legacy_adapter: Adapter used when no active spec exists in the
            store. Defaults to a fresh :class:`LegacyStrategyAdapter`.
            Pass ``None`` explicitly via :meth:`__init__` only when the
            caller wants ``select`` to raise :class:`NoActiveSpecError`
            on a miss instead of returning the legacy spec; production
            code should not do this.
        cache_ttl_seconds: TTL for store-resolved entries.
        fallback_ttl_seconds: TTL for legacy-adapter-resolved entries.
        db: **Deprecated** back-compat shim. When ``store`` is ``None``
            but ``db`` is supplied, the selector wraps it in a
            :class:`MongoSpecStore`. New code should pass ``store``
            directly.
    """

    _SENTINEL: Any = object()

    def __init__(
        self,
        store: Optional[SpecStore] = None,
        *,
        legacy_adapter: Any = _SENTINEL,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        fallback_ttl_seconds: int = FALLBACK_CACHE_TTL_SECONDS,
        db: Any = None,
    ) -> None:
        if store is None and db is not None:
            # Back-compat: callers used to pass a raw async DB handle.
            mongo_db = getattr(db, "db", db)
            store = MongoSpecStore(mongo_db)
        self.store: Optional[SpecStore] = store
        if legacy_adapter is StrategySpecSelector._SENTINEL:
            self.legacy_adapter: Optional[LegacyStrategyAdapter] = LegacyStrategyAdapter()
        else:
            self.legacy_adapter = legacy_adapter
        self._cache_ttl = int(cache_ttl_seconds)
        self._fallback_ttl = int(fallback_ttl_seconds)
        self._cache: dict[tuple[Optional[str], Optional[str]], _CacheEntry] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    async def select(
        self,
        capability_id: Optional[str],
        agent_mode: str = "auto",
        tenant_id: Optional[str] = "recallhub",
        privacy_mode: str = "standard",
        business_context: Optional[BusinessContext] = None,
    ) -> Optional[StrategySpec]:
        """Return the best matching :class:`StrategySpec` for the request.

        Args:
            capability_id: Capability id detected by the business-context
                resolver. ``None`` short-circuits to the legacy fallback
                path because the persisted store keys on capability.
            agent_mode: ``"auto"`` / ``"fast"`` / ``"deep"`` execution
                preference. Used to filter store results post-fetch.
            tenant_id: Tenant scope used both as a cache key component
                and as the preferred ``tenant_scope`` match.
            privacy_mode: Privacy compliance constraint. ``"local_only"``
                excludes specs whose budgets do not declare ``local_only``.
            business_context: Optional resolved business context. Not
                used for selection ranking in the DB-backed path; kept
                for API stability with the original Phase 3 selector.

        Returns:
            The selected :class:`StrategySpec`, or ``None`` when no spec
            is available and no legacy adapter is configured.

        Raises:
            NoActiveSpecError: When no active spec exists in the store,
                the legacy adapter is configured, and the legacy adapter
                also returned ``None``. (The default
                :class:`LegacyStrategyAdapter` always returns a spec, so
                this path is exercised mainly by tests.)
        """
        cache_key = (capability_id, tenant_id)
        now = time.time()

        cached = self._cache.get(cache_key)
        if cached is not None and cached.is_fresh(now):
            spec = cached.spec
            if not self._passes_runtime_filters(spec, agent_mode, privacy_mode):
                # Cached spec no longer satisfies the requested runtime
                # constraints (e.g. privacy_mode tightened on this call).
                # Fall through to a fresh resolution rather than returning
                # an inappropriate spec.
                pass
            else:
                return spec

        spec = await self._resolve_from_store(
            capability_id=capability_id,
            tenant_id=tenant_id,
            agent_mode=agent_mode,
            privacy_mode=privacy_mode,
        )
        if spec is not None:
            self._cache[cache_key] = _CacheEntry(
                spec=spec,
                expires_at=now + self._cache_ttl,
                from_fallback=False,
            )
            logger.info(
                "Selected strategy spec %s for capability_id=%s tenant_id=%s",
                spec.strategy_id,
                capability_id,
                tenant_id,
            )
            return spec

        logger.warning(
            "no active spec for capability_id=%s tenant_id=%s, "
            "falling back to legacy adapter",
            capability_id,
            tenant_id,
        )
        fallback_spec = self._resolve_from_legacy()
        if fallback_spec is None:
            if self.legacy_adapter is None:
                # Caller explicitly disabled the fallback; preserve the
                # historical "return None" behaviour for that case.
                return None
            raise NoActiveSpecError(capability_id, tenant_id)

        self._cache[cache_key] = _CacheEntry(
            spec=fallback_spec,
            expires_at=now + self._fallback_ttl,
            from_fallback=True,
        )
        return fallback_spec

    def clear_cache(self, capability_id: Optional[str] = None, tenant_id: Optional[str] = None) -> int:
        """Drop cached entries.

        Args:
            capability_id: When provided, drop only entries with this
                capability id. ``None`` (default) clears all entries.
            tenant_id: When provided alongside ``capability_id``, drop
                only the specific ``(capability_id, tenant_id)`` entry.

        Returns:
            The number of entries removed.
        """
        if capability_id is None and tenant_id is None:
            removed = len(self._cache)
            self._cache.clear()
            return removed

        to_drop: list[tuple[Optional[str], Optional[str]]] = []
        for key in self._cache:
            cap, tn = key
            if capability_id is not None and cap != capability_id:
                continue
            if tenant_id is not None and tn != tenant_id:
                continue
            to_drop.append(key)
        for key in to_drop:
            self._cache.pop(key, None)
        return len(to_drop)

    # Backwards-compatible alias used by older tests / call sites.
    def invalidate_cache(self) -> None:
        """Clear all cached entries (legacy alias for :meth:`clear_cache`)."""
        self.clear_cache()

    async def list_active_for_capability(
        self, capability_id: Optional[str]
    ) -> list[StrategySpec]:
        """Return every active :class:`StrategySpec` for ``capability_id``.

        Used by :class:`AdaptiveSelector` to obtain the candidate set
        before scoring. Hydrates each store snapshot through
        :meth:`_snapshot_to_spec` and silently skips malformed rows.
        Filtering by tenant/agent_mode/privacy is intentionally **not**
        applied here — the adaptive layer decides which constraints to
        enforce as hard disqualifications versus soft penalties.

        Args:
            capability_id: Capability id to filter on. ``None`` or an
                absent store both return an empty list.

        Returns:
            A list of hydrated :class:`StrategySpec` objects. Empty
            when no candidates exist or the store call fails.
        """
        if self.store is None or capability_id is None:
            return []
        try:
            snapshots = await self.store.list(
                status="active",
                capability_id=capability_id,
                limit=100,
            )
        except Exception as exc:  # noqa: BLE001 — never propagate store errors
            logger.warning(
                "list_active_for_capability(%s) failed: %s", capability_id, exc
            )
            return []
        out: list[StrategySpec] = []
        for snap in snapshots:
            spec = self._snapshot_to_spec(snap)
            if spec is not None:
                out.append(spec)
        return out

    def rank_candidates(
        self, candidates: list[StrategySpec], *, tenant_id: Optional[str]
    ) -> StrategySpec:
        """Public wrapper around the deterministic tenant/recency ranker.

        Re-exposes :meth:`_rank_candidates` so :class:`AdaptiveSelector`
        can defer to the same tie-break rules used by the base path.
        """
        return self._rank_candidates(candidates, tenant_id=tenant_id)

    # ── Internal helpers ────────────────────────────────────────────────────

    async def _resolve_from_store(
        self,
        *,
        capability_id: Optional[str],
        tenant_id: Optional[str],
        agent_mode: str,
        privacy_mode: str,
    ) -> Optional[StrategySpec]:
        """Fetch active candidates from the store and rank them."""
        if self.store is None or capability_id is None:
            return None

        try:
            snapshots = await self.store.list(
                status="active",
                capability_id=capability_id,
                limit=100,
            )
        except Exception as exc:  # noqa: BLE001 — never propagate store errors
            logger.warning(
                "Spec store list failed for capability_id=%s tenant_id=%s: %s",
                capability_id,
                tenant_id,
                exc,
            )
            return None

        if not snapshots:
            return None

        candidates: list[StrategySpec] = []
        for snap in snapshots:
            spec = self._snapshot_to_spec(snap)
            if spec is None:
                continue
            if not self._passes_runtime_filters(spec, agent_mode, privacy_mode):
                continue
            candidates.append(spec)

        if not candidates:
            return None
        return self._rank_candidates(candidates, tenant_id=tenant_id)

    def _resolve_from_legacy(self) -> Optional[StrategySpec]:
        """Pull a spec from the legacy adapter if one is configured."""
        if self.legacy_adapter is None:
            return None
        try:
            return self.legacy_adapter.get_spec()
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning("Legacy adapter spec resolution failed: %s", exc)
            return None

    @staticmethod
    def _snapshot_to_spec(snapshot: dict[str, Any]) -> Optional[StrategySpec]:
        """Hydrate a :class:`StrategySpec` from a store snapshot dict."""
        spec_data = snapshot.get("spec_data") if isinstance(snapshot, dict) else None
        if not isinstance(spec_data, dict):
            return None
        # Make sure status reflects the snapshot envelope (the store-side
        # status is authoritative; spec_data may lag during in-place edits).
        status = snapshot.get("status") or spec_data.get("status")
        if status:
            spec_data = {**spec_data, "status": status}
        try:
            return StrategySpec(**spec_data)
        except Exception as exc:  # noqa: BLE001 — bad row should not crash
            logger.warning(
                "Skipping malformed strategy spec snapshot for strategy_id=%s: %s",
                spec_data.get("strategy_id"),
                exc,
            )
            return None

    @staticmethod
    def _passes_runtime_filters(
        spec: StrategySpec, agent_mode: str, privacy_mode: str
    ) -> bool:
        """Apply privacy and agent-mode constraints to a candidate spec."""
        if privacy_mode == "local_only" and not spec.budgets.local_only:
            return False
        latency = spec.budgets.latency_target_ms
        if agent_mode == "fast" and latency > 5000:
            return False
        if agent_mode == "deep" and latency < 3000:
            return False
        return True

    @staticmethod
    def _rank_candidates(
        candidates: list[StrategySpec], *, tenant_id: Optional[str]
    ) -> StrategySpec:
        """Pick the best candidate deterministically.

        Ranking:

        1. Exact ``tenant_scope == tenant_id`` wins.
        2. Then most recent ``updated_at`` (falling back to ``created_at``).
        3. Then ``strategy_id`` ascending for a stable tie-break.
        """

        def _timestamp(spec: StrategySpec) -> datetime:
            return spec.updated_at or spec.created_at or datetime.min

        def _key(spec: StrategySpec) -> tuple[int, float, str]:
            tenant_match = 0 if (tenant_id and spec.tenant_scope == tenant_id) else 1
            # Negative timestamp so descending order falls out of ascending sort.
            ts = -_timestamp(spec).timestamp() if _timestamp(spec) != datetime.min else 0.0
            return (tenant_match, ts, spec.strategy_id)

        ranked = sorted(candidates, key=_key)
        return ranked[0]


# ═══ Adaptive layer (Phase 6 / Task 78) ═══════════════════════════════════════


class FastPathRules:
    """Pure-logic helper that decides whether to skip the orchestrator.

    Implements the five conditions from Blueprint 08 §9. ``evaluate``
    returns ``True`` only when **all** conditions hold; missing
    optional inputs are treated conservatively (i.e. they fail the
    relevant condition rather than being assumed safe).

    Args:
        eligible_capabilities: Capabilities that are allowed to take
            the fast path when the answer contract does not declare an
            explicit ``has_template`` flag. Defaults to
            :data:`FAST_PATH_ELIGIBLE_CAPABILITIES`.
        retrieval_confidence_threshold: Minimum top-1 retrieval score
            required to consider the result confident enough to bypass
            the full DAG. Defaults to ``0.85`` per the blueprint.

    Note:
        ``business_context.answer_contract`` does **not** currently
        expose a ``has_template`` boolean field. When the field is
        present we honour it; otherwise we fall back to the hardcoded
        ``eligible_capabilities`` allow-list. This is documented as a
        follow-up under the Phase 6 deliverables.
    """

    DRAFTING_SUFFIXES: tuple[str, ...] = ("_questions", "_summary", "_draft")

    def __init__(
        self,
        *,
        eligible_capabilities: Optional[frozenset[str]] = None,
        retrieval_confidence_threshold: float = 0.85,
    ) -> None:
        self.eligible_capabilities = (
            eligible_capabilities
            if eligible_capabilities is not None
            else FAST_PATH_ELIGIBLE_CAPABILITIES
        )
        self.retrieval_confidence_threshold = float(retrieval_confidence_threshold)

    def evaluate(
        self,
        *,
        business_context: Optional[BusinessContext],
        source_policy: Any,
        capability_id: Optional[str],
        top_retrieval_score: Optional[float],
    ) -> bool:
        """Return ``True`` when every fast-path precondition holds.

        Conditions (Blueprint 08 §9):

        1. Single, unambiguous active matter / profile on the
           :class:`BusinessContext`.
        2. Top retrieval score ≥ ``retrieval_confidence_threshold``;
           ``None`` is treated as "unknown" → fails.
        3. Capability has a template answer contract. Honoured via
           ``answer_contract.has_template`` when present, else falls
           back to :attr:`eligible_capabilities`.
        4. ``source_policy.allow_web`` is falsy (no external facts
           required).
        5. Capability id ends with one of :attr:`DRAFTING_SUFFIXES`
           (drafting / summarisation heuristic).
        """
        # Condition 1: Single, unambiguous matter/profile.
        if business_context is None:
            return False
        active_matter_id = getattr(business_context, "matter_id", None)
        if not active_matter_id:
            return False
        ambiguity = getattr(business_context, "ambiguity", None)
        if ambiguity is not None and getattr(ambiguity, "ambiguity_detected", False):
            return False

        # Condition 2: High retrieval confidence.
        if top_retrieval_score is None:
            return False
        if float(top_retrieval_score) < self.retrieval_confidence_threshold:
            return False

        # Condition 3: Template answer contract.
        if not capability_id:
            return False
        answer_contract = getattr(business_context, "answer_contract", None)
        has_template_flag = getattr(answer_contract, "has_template", None)
        if has_template_flag is None:
            # No explicit field on the contract → fall back to allow-list.
            if capability_id not in self.eligible_capabilities:
                return False
        elif not bool(has_template_flag):
            return False

        # Condition 4: No external web facts required.
        if source_policy is None:
            return False
        allow_web = getattr(source_policy, "allow_web", None)
        if allow_web is None and isinstance(source_policy, dict):
            allow_web = source_policy.get("allow_web")
        if bool(allow_web):
            return False

        # Condition 5: Drafting / summarisation heuristic.
        if not any(
            capability_id.endswith(suffix) for suffix in self.DRAFTING_SUFFIXES
        ):
            return False

        return True


class AdaptiveSelector:
    """Decorator around :class:`StrategySpecSelector` that routes adaptively.

    Layers four runtime signals on top of the deterministic Phase 5
    selector:

    * **Latency-fit** — derived from
      :class:`~backend.agent.strategy.runtime_profile_store.RuntimeProfileStore`.
    * **Quality** — mean ``composite_score`` over the last 30 days from
      the ``evaluation_results`` collection (Phase 3 / Task 30).
    * **Resource-fit** — from
      :class:`~backend.services.resource_snapshot.ResourceSnapshotCollector`.
    * **Residency** — bonus when the candidate's synth model is in
      ``ResourceSnapshot.ollama_resident_models``.

    Privacy is enforced as a hard disqualification: a ``strict``
    privacy mode excludes any candidate whose source policy permits
    web access.

    The total routing score is a weighted sum:

    ``score = 0.4 * latency_fit + 0.3 * quality + 0.2 * resource_fit + 0.1 * residency``

    The same ``(capability_id, tenant_id, privacy_mode)`` cache as the
    base selector is used; :meth:`clear_cache` flushes both layers so
    promotion endpoints continue to invalidate selection state
    correctly.

    All optional dependencies are nullable: when every adaptive input
    is ``None`` the call falls through to ``base_selector.select``
    unchanged (backward-compatible).

    Args:
        base_selector: The Phase 5 :class:`StrategySpecSelector` this
            decorator wraps. Must not be ``None``.
        runtime_profile_store: Optional Phase 6 / P1 runtime profile
            source. ``None`` disables the latency-fit signal.
        resource_collector: Optional Phase 6 / P2 resource collector.
            ``None`` disables the resource-fit and residency signals.
        evaluation_results_db: Optional async Mongo database handle
            (PyMongo Async, repo rule #3) exposing the
            ``evaluation_results`` collection. ``None`` disables the
            quality signal.
        fast_path_rules: Custom :class:`FastPathRules` instance.
            Defaults to :class:`FastPathRules` with default thresholds.
        cache_ttl_seconds: TTL for adaptive decisions. Defaults to
            :data:`DEFAULT_CACHE_TTL_SECONDS`.
        quality_lookback_days: Window applied to the ``evaluation_results``
            aggregation. Defaults to ``30``.
        decision_store: Optional :class:`AdaptiveDecisionStore` (F8 /
            Task 85) into which each routing decision is persisted for
            post-hoc analysis. ``None`` disables persistence; the
            existing operational-trace event is still emitted.
            Persistence failures are best-effort and never raise out
            of :meth:`select`.
        record_cache_hits: When ``True`` (default) cache-hit decisions
            are also persisted with ``chosen_via_cache=True`` so
            operators can measure cache-hit ratios. Set to ``False`` to
            persist only fresh decisions.
    """

    #: Routing score weights (must sum to 1.0).
    LATENCY_WEIGHT: float = 0.4
    QUALITY_WEIGHT: float = 0.3
    RESOURCE_WEIGHT: float = 0.2
    RESIDENCY_WEIGHT: float = 0.1

    #: Strong negative score returned when a candidate is disqualified
    #: by a hard rule (e.g. privacy mismatch, latency hard-limit).
    DISQUALIFY_SCORE: float = -1e6

    def __init__(
        self,
        *,
        base_selector: StrategySpecSelector,
        runtime_profile_store: Any = None,
        resource_collector: Any = None,
        evaluation_results_db: Any = None,
        fast_path_rules: Optional[FastPathRules] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        quality_lookback_days: int = 30,
        decision_store: Optional[AdaptiveDecisionStore] = None,
        record_cache_hits: bool = True,
    ) -> None:
        if base_selector is None:
            raise ValueError("AdaptiveSelector requires a non-None base_selector")
        self.base_selector = base_selector
        self.runtime_profile_store = runtime_profile_store
        self.resource_collector = resource_collector
        self.evaluation_results_db = evaluation_results_db
        self.fast_path_rules = fast_path_rules or FastPathRules()
        self._cache_ttl = int(cache_ttl_seconds)
        self._quality_lookback_days = int(quality_lookback_days)
        self.decision_store = decision_store
        self.record_cache_hits = bool(record_cache_hits)
        self._cache: dict[
            tuple[Optional[str], Optional[str], Optional[str]], _CacheEntry
        ] = {}

    # ── Public API ───────────────────────────────────────────────────────

    async def select(
        self,
        *,
        capability_id: Optional[str],
        agent_mode: str = "auto",
        tenant_id: Optional[str] = "recallhub",
        privacy_mode: str = "standard",
        business_context: Optional[BusinessContext] = None,
        top_retrieval_score: Optional[float] = None,
    ) -> Optional[StrategySpec]:
        """Pick the best candidate spec using adaptive routing.

        Falls back to ``base_selector.select`` when the wrapped
        selector cannot enumerate multiple active candidates (legacy
        callers and adapter-only paths preserve their behaviour).

        Args:
            capability_id: Capability id resolved by
                :class:`BusinessContextResolver`.
            agent_mode: ``auto`` / ``fast`` / ``deep`` execution
                preference. Forwarded to the base selector for
                compatibility.
            tenant_id: Tenant scope used for cache keying and tenant
                preference in tie-breaks.
            privacy_mode: ``standard`` / ``strict`` / ``local_only``.
                ``strict`` disqualifies web-enabled specs.
            business_context: Resolved business context. Required for
                fast-path evaluation; ignored otherwise.
            top_retrieval_score: Optional top-1 retrieval confidence
                used to gate the fast path.

        Returns:
            The selected :class:`StrategySpec`, or ``None`` when no
            candidates exist and the base selector also has no
            fallback.

        Raises:
            NoActiveSpecError: Surfaced from the base selector when no
                spec can be resolved at all.
        """
        cache_key = (capability_id, tenant_id, privacy_mode)
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached is not None and cached.is_fresh(now):
            if self.decision_store is not None and self.record_cache_hits and self._cache_ttl > 0:
                await self._persist_decision(
                    capability_id=capability_id,
                    tenant_id=tenant_id,
                    privacy_mode=privacy_mode,
                    agent_mode=agent_mode,
                    winner=cached.spec,
                    scored=None,
                    fast_path_eligible=False,
                    chosen_via_cache=True,
                )
            return cached.spec

        candidates = await self.base_selector.list_active_for_capability(
            capability_id
        )

        if not candidates:
            # No multi-candidate set → defer entirely to the base
            # selector (legacy adapter, NoActiveSpecError, etc.).
            return await self.base_selector.select(
                capability_id=capability_id,
                agent_mode=agent_mode,
                tenant_id=tenant_id,
                privacy_mode=privacy_mode,
                business_context=business_context,
            )

        if len(candidates) == 1:
            spec = candidates[0]
            self._cache[cache_key] = _CacheEntry(
                spec=spec, expires_at=now + self._cache_ttl, from_fallback=False
            )
            return spec

        # Determine fast-path eligibility once — used as a soft bias
        # toward "fast" variants (low latency target / spec name
        # contains "fast") rather than as an unconditional override.
        source_policy = None
        if business_context is not None:
            source_policy = getattr(
                business_context, "resolved_source_policy", None
            )
        fast_path_eligible = self.fast_path_rules.evaluate(
            business_context=business_context,
            source_policy=source_policy,
            capability_id=capability_id,
            top_retrieval_score=top_retrieval_score,
        )

        # Pre-fetch shared signals once per call.
        snapshot = await self._fetch_resource_snapshot()
        quality_by_strategy = await self._fetch_quality_scores(capability_id)

        scored: list[tuple[StrategySpec, float, dict[str, Any]]] = []
        for spec in candidates:
            score, breakdown = await self._score_candidate(
                spec=spec,
                privacy_mode=privacy_mode,
                snapshot=snapshot,
                quality_by_strategy=quality_by_strategy,
                fast_path_eligible=fast_path_eligible,
            )
            scored.append((spec, score, breakdown))

        # Filter out hard-disqualified candidates.
        eligible = [s for s in scored if s[1] > self.DISQUALIFY_SCORE / 2]
        if not eligible:
            # Every candidate failed a hard rule — fall back to base
            # selector (which may still produce a legacy spec).
            return await self.base_selector.select(
                capability_id=capability_id,
                agent_mode=agent_mode,
                tenant_id=tenant_id,
                privacy_mode=privacy_mode,
                business_context=business_context,
            )

        # Sort by score desc; tie-break via base selector's deterministic
        # tenant/recency ranker over the top-scoring group.
        max_score = max(s[1] for s in eligible)
        top_group = [s[0] for s in eligible if s[1] == max_score]
        if len(top_group) == 1:
            winner = top_group[0]
        else:
            winner = self.base_selector.rank_candidates(
                top_group, tenant_id=tenant_id
            )

        self._cache[cache_key] = _CacheEntry(
            spec=winner, expires_at=now + self._cache_ttl, from_fallback=False
        )

        # Persist the decision (F8 / Task 85). Best-effort: never raise
        # out of select() on store failure.
        await self._persist_decision(
            capability_id=capability_id,
            tenant_id=tenant_id,
            privacy_mode=privacy_mode,
            agent_mode=agent_mode,
            winner=winner,
            scored=scored,
            fast_path_eligible=fast_path_eligible,
            chosen_via_cache=False,
        )

        # Telemetry: emit through the operational_trace surface.
        try:
            emit_trace_event(
                None,
                "adaptive_selection_decision",
                {
                    "capability_id": capability_id,
                    "tenant_id": tenant_id,
                    "privacy_mode": privacy_mode,
                    "fast_path_eligible": fast_path_eligible,
                    "winner_strategy_id": winner.strategy_id,
                    "candidates": [
                        {
                            "strategy_id": spec.strategy_id,
                            "score": round(score, 4),
                            "breakdown": breakdown,
                        }
                        for spec, score, breakdown in scored
                    ],
                },
            )
        except Exception as exc:  # noqa: BLE001 — telemetry must never crash select()
            logger.warning("adaptive_selection_decision emit failed: %s", exc)

        return winner

    def clear_cache(
        self,
        capability_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> int:
        """Clear cached entries on **both** layers.

        Mirrors :meth:`StrategySpecSelector.clear_cache` so promotion
        endpoints can invalidate adaptive decisions in the same call.

        Args:
            capability_id: Optional capability filter.
            tenant_id: Optional tenant filter (paired with
                ``capability_id``).

        Returns:
            Total number of entries removed across both layers.
        """
        removed_base = self.base_selector.clear_cache(
            capability_id=capability_id, tenant_id=tenant_id
        )
        if capability_id is None and tenant_id is None:
            removed_self = len(self._cache)
            self._cache.clear()
            return removed_base + removed_self
        to_drop: list[
            tuple[Optional[str], Optional[str], Optional[str]]
        ] = []
        for key in self._cache:
            cap, tn, _privacy = key
            if capability_id is not None and cap != capability_id:
                continue
            if tenant_id is not None and tn != tenant_id:
                continue
            to_drop.append(key)
        for key in to_drop:
            self._cache.pop(key, None)
        return removed_base + len(to_drop)

    def invalidate_cache(self) -> None:
        """Clear all cached entries (legacy alias for :meth:`clear_cache`)."""
        self.clear_cache()

    async def _persist_decision(
        self,
        *,
        capability_id: Optional[str],
        tenant_id: Optional[str],
        privacy_mode: Optional[str],
        agent_mode: Optional[str],
        winner: StrategySpec,
        scored: Optional[list[tuple[StrategySpec, float, dict[str, Any]]]],
        fast_path_eligible: bool,
        chosen_via_cache: bool,
    ) -> None:
        """Best-effort persist of an :class:`AdaptiveDecisionDoc`.

        The selector decision must always be returned to the caller, so
        any exception raised by the store is logged and swallowed. The
        existing operational-trace event is emitted independently of
        this call.

        Args:
            capability_id: Capability the decision was made for.
            tenant_id: Tenant scope.
            privacy_mode: Privacy preference forwarded by the caller.
            agent_mode: ``auto`` / ``fast`` / ``deep`` preference.
            winner: The chosen :class:`StrategySpec`.
            scored: Per-candidate ``(spec, score, breakdown)`` tuples.
                ``None`` when persisting a cache-hit doc.
            fast_path_eligible: Fast-path-rules verdict for the request.
            chosen_via_cache: ``True`` when ``winner`` came from the
                adaptive cache rather than fresh scoring.
        """
        store = self.decision_store
        if store is None:
            return
        if capability_id is None:
            return
        try:
            doc = AdaptiveDecisionDoc(
                capability_id=capability_id,
                tenant_id=tenant_id,
                privacy_mode=privacy_mode,
                agent_mode=agent_mode,
                winner_strategy_id=winner.strategy_id,
                candidate_scores=_build_candidate_scores(scored),
                fast_path_eligible=bool(fast_path_eligible),
                chosen_via_cache=bool(chosen_via_cache),
            )
            await store.record(doc)
        except Exception as exc:  # noqa: BLE001 - selection must never fail on persistence
            logger.warning(
                "AdaptiveDecisionStore.record failed (non-fatal): %s", exc
            )

    async def _fetch_resource_snapshot(self) -> Optional[Any]:
        """Return the latest resource snapshot or ``None`` on failure."""
        if self.resource_collector is None:
            return None
        try:
            return await self.resource_collector.latest()
        except Exception as exc:  # noqa: BLE001
            logger.warning("resource_collector.latest() failed: %s", exc)
            return None

    async def _fetch_quality_scores(
        self, capability_id: Optional[str]
    ) -> dict[str, float]:
        """Aggregate mean ``composite_score`` per ``strategy_id``.

        Reads from the ``evaluation_results`` collection. The doc
        shape follows :class:`backend.evaluation.models.EvaluationRun`
        — ``result.composite_score`` is the canonical field; we also
        accept a top-level ``composite_score`` for forward
        compatibility.
        """
        out: dict[str, list[float]] = {}
        if self.evaluation_results_db is None or capability_id is None:
            return {}
        since = datetime.now(timezone.utc) - timedelta(
            days=self._quality_lookback_days
        )
        try:
            collection = self.evaluation_results_db["evaluation_results"]
            cursor = collection.find({"created_at": {"$gte": since}})
            async for doc in cursor:
                strategy_id = doc.get("strategy_id")
                if not isinstance(strategy_id, str):
                    continue
                score = None
                result = doc.get("result")
                if isinstance(result, dict):
                    score = result.get("composite_score")
                if score is None:
                    score = doc.get("composite_score")
                if not isinstance(score, (int, float)):
                    continue
                out.setdefault(strategy_id, []).append(float(score))
        except Exception as exc:  # noqa: BLE001
            logger.warning("evaluation_results aggregation failed: %s", exc)
            return {}
        return {sid: sum(vals) / len(vals) for sid, vals in out.items() if vals}

    async def _fetch_latency_signal(
        self, spec: StrategySpec
    ) -> Optional[tuple[float, float]]:
        """Return ``(mean_tps, expected_wall_ms)`` for ``spec`` or ``None``.

        ``expected_wall_ms`` is computed from the spec's
        ``budgets.max_output_tokens`` divided by the mean
        ``tokens_per_second`` observed in the last 7 days for the
        candidate's synth model.
        """
        if self.runtime_profile_store is None:
            return None
        synth_model = _resolve_synth_model(spec)
        if not synth_model:
            return None
        since = datetime.now(timezone.utc) - timedelta(days=7)
        try:
            profiles = await self.runtime_profile_store.list_recent(
                model=synth_model, since=since, limit=50
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "runtime_profile_store.list_recent(%s) failed: %s",
                synth_model,
                exc,
            )
            return None
        if not profiles:
            return None
        tps_values = [
            float(getattr(p, "tokens_per_second", 0.0) or 0.0)
            for p in profiles
        ]
        tps_values = [v for v in tps_values if v > 0]
        if not tps_values:
            return None
        mean_tps = sum(tps_values) / len(tps_values)
        output_tokens = max(int(spec.budgets.max_output_tokens or 0), 1)
        expected_wall_ms = (output_tokens / mean_tps) * 1000.0
        return (mean_tps, expected_wall_ms)

    async def _score_candidate(
        self,
        *,
        spec: StrategySpec,
        privacy_mode: str,
        snapshot: Any,
        quality_by_strategy: dict[str, float],
        fast_path_eligible: bool,
    ) -> tuple[float, dict[str, Any]]:
        """Compute the routing score + breakdown for a single candidate."""
        breakdown: dict[str, Any] = {}

        # Privacy hard-disqualification.
        if privacy_mode == "strict":
            allow_web = False
            sp = spec.source_policy
            if sp is not None and getattr(sp, "allow_web", False):
                allow_web = True
            if allow_web:
                breakdown["disqualified"] = "privacy_strict_web_disallowed"
                return self.DISQUALIFY_SCORE, breakdown

        # Latency-fit.
        latency_signal = await self._fetch_latency_signal(spec)
        if latency_signal is None:
            latency_fit = 0.5  # Neutral when no profile data.
            breakdown["latency"] = {"signal": "none", "fit": 0.5}
        else:
            mean_tps, expected_wall_ms = latency_signal
            target = float(spec.budgets.latency_target_ms)
            hard = float(spec.budgets.latency_hard_limit_ms)
            if expected_wall_ms > hard:
                breakdown["disqualified"] = "latency_hard_limit_exceeded"
                breakdown["latency"] = {
                    "signal": "hard_breach",
                    "expected_ms": expected_wall_ms,
                    "hard_ms": hard,
                }
                return self.DISQUALIFY_SCORE, breakdown
            if expected_wall_ms <= target:
                latency_fit = 1.0
            else:
                # Linear decay from 1.0 at target to 0.0 at hard limit.
                span = max(hard - target, 1.0)
                latency_fit = max(0.0, 1.0 - (expected_wall_ms - target) / span)
            breakdown["latency"] = {
                "signal": "ok",
                "mean_tps": mean_tps,
                "expected_ms": expected_wall_ms,
                "target_ms": target,
                "fit": latency_fit,
            }

        # Quality.
        if quality_by_strategy:
            quality = quality_by_strategy.get(spec.strategy_id)
            if quality is None:
                quality = 0.5
                breakdown["quality"] = {"signal": "missing", "score": 0.5}
            else:
                quality = max(0.0, min(1.0, float(quality)))
                breakdown["quality"] = {"signal": "ok", "score": quality}
        else:
            quality = 0.5
            breakdown["quality"] = {"signal": "none", "score": 0.5}

        # Resource-fit + residency.
        resource_fit = 1.0
        residency = 0.0
        if snapshot is not None:
            synth_model = _resolve_synth_model(spec)
            resident = list(
                getattr(snapshot, "ollama_resident_models", []) or []
            )
            if synth_model and synth_model in resident:
                residency = 1.0
                breakdown["residency"] = {"signal": "resident", "bonus": 1.0}
            else:
                breakdown["residency"] = {"signal": "cold", "bonus": 0.0}
            gpu_pct = getattr(snapshot, "gpu_pct", None)
            cpu_pct = getattr(snapshot, "cpu_pct", None)
            spec_uses_accelerator = bool(synth_model)
            if (
                gpu_pct is not None
                and float(gpu_pct) > 80.0
                and spec_uses_accelerator
            ):
                resource_fit -= 0.5
            if (
                spec.budgets.local_only
                and cpu_pct is not None
                and float(cpu_pct) > 90.0
            ):
                resource_fit -= 0.2
            resource_fit = max(0.0, resource_fit)
            breakdown["resource"] = {
                "gpu_pct": gpu_pct,
                "cpu_pct": cpu_pct,
                "fit": resource_fit,
            }
        else:
            breakdown["resource"] = {"signal": "none", "fit": 1.0}
            breakdown["residency"] = {"signal": "none", "bonus": 0.0}

        score = (
            self.LATENCY_WEIGHT * latency_fit
            + self.QUALITY_WEIGHT * quality
            + self.RESOURCE_WEIGHT * resource_fit
            + self.RESIDENCY_WEIGHT * residency
        )

        # Soft fast-path bias — prefer "fast" variants when the
        # rules engine signalled an eligible request.
        if fast_path_eligible and _looks_fast_variant(spec):
            score += 0.05
            breakdown["fast_path_bias"] = 0.05

        breakdown["total"] = score
        return score, breakdown


def _build_candidate_scores(
    scored: Optional[list[tuple[StrategySpec, float, dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Flatten the per-candidate ``(spec, score, breakdown)`` tuples.

    The shape mirrors the operational-trace ``candidates`` payload but
    promotes the most analytically useful fields to the top level so
    Mongo aggregations don't need to dig through ``breakdown``.
    """
    if not scored:
        return []
    out: list[dict[str, Any]] = []
    for spec, score, breakdown in scored:
        latency_block = breakdown.get("latency") if isinstance(breakdown, dict) else None
        quality_block = breakdown.get("quality") if isinstance(breakdown, dict) else None
        resource_block = breakdown.get("resource") if isinstance(breakdown, dict) else None
        residency_block = breakdown.get("residency") if isinstance(breakdown, dict) else None
        disqualified_reason = (
            breakdown.get("disqualified") if isinstance(breakdown, dict) else None
        )
        out.append(
            {
                "strategy_id": spec.strategy_id,
                "latency_fit": (
                    latency_block.get("fit")
                    if isinstance(latency_block, dict)
                    else None
                ),
                "quality": (
                    quality_block.get("score")
                    if isinstance(quality_block, dict)
                    else None
                ),
                "resource_fit": (
                    resource_block.get("fit")
                    if isinstance(resource_block, dict)
                    else None
                ),
                "residency": (
                    residency_block.get("bonus")
                    if isinstance(residency_block, dict)
                    else None
                ),
                "fast_path_bias": (
                    breakdown.get("fast_path_bias")
                    if isinstance(breakdown, dict)
                    else None
                ),
                "total": float(score),
                "disqualified": bool(disqualified_reason),
                "disqualified_reason": disqualified_reason,
            }
        )
    return out


def _resolve_synth_model(spec: StrategySpec) -> Optional[str]:
    """Return the spec's preferred synthesizer model, if any.

    Looks at ``spec.model_roles`` for common synth keys and falls back
    to the first declared role. Returns ``None`` when no role is
    declared.
    """
    roles = spec.model_roles or {}
    for key in ("synth", "synthesizer", "synthesis", "primary"):
        value = roles.get(key)
        if isinstance(value, str) and value:
            return value
    for value in roles.values():
        if isinstance(value, str) and value:
            return value
    return None


def _looks_fast_variant(spec: StrategySpec) -> bool:
    """Heuristic: whether ``spec`` is the "fast" sibling in a multi-spec set."""
    name = (spec.display_name or spec.strategy_id or "").lower()
    if "fast" in name:
        return True
    if spec.budgets.latency_target_ms <= 5000:
        return True
    return False
