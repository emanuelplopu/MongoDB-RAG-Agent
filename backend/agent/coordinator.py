"""Federated Agent Coordinator - Main entry point for the agent system.

The FederatedAgent coordinates the Orchestrator and WorkerPool to:
1. Process user messages
2. Execute multi-step search and reasoning
3. Generate comprehensive responses
4. Maintain full trace for transparency
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Callable, Awaitable

# Type alias for event callback: async function that takes event_type and event_data
EventCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]

from backend.agent.schemas import (
    AgentModeConfig, AgentMode, AgentTrace, AgentPlan,
    TaskDefinition, TaskType, WorkerResult, DocumentReference,
    DataSource, EvaluationDecision, ResultQuality, StrategySelection
)
from backend.agent.orchestrator import Orchestrator
from backend.agent.worker_pool import WorkerPool
from backend.agent.federated_search import FederatedSearch, get_federated_search
from backend.agent.strategy.business_context_resolver import BusinessContextResolver
from backend.agent.strategy.models import SearchRequest as StrategySearchRequest, BusinessContext
from backend.agent.strategy.strategy_runner import StrategyRunner, StrategyExecutionError
from backend.agent.strategy.spec_selector import (
    AdaptiveSelector,
    StrategySpecSelector,
)
from backend.agent.strategy.strategy_trace import (
    ROUTING_REASON_ADAPTIVE,
    ROUTING_REASON_CAPABILITY_NOT_MATCHED,
    ROUTING_REASON_DAG_FAILED,
    ROUTING_REASON_DISABLED,
    ROUTING_REASON_LEGACY_FALLBACK,
    StrategyTraceResponse,
    build_strategy_trace,
)
from backend.agent.strategy.nodes import create_default_registry, NODE_TYPE_REGISTRY
from backend.evaluation.contradiction_detector import ContradictionDetector
from backend.agent.strategies.base import BaseStrategy
from backend.agent.strategies.registry import StrategyRegistry
from backend.agent.tool_gate import ToolGate
from backend.core.config import settings
from backend.services.activity_logger import ActivityLogger, NullActivityLogger
from backend.models.telemetry import LLMCall, SearchOperation, ToolExecution, PhaseMetrics
try:
    from backend.routers.prompts import get_agent_prompt_sync
except ImportError:
    def get_agent_prompt_sync(prompt_key: str) -> str:
        """Fallback used in tests when prompt router dependencies are unavailable."""
        return ""

logger = logging.getLogger(__name__)

# Module-level telemetry service reference, set during app lifespan
_telemetry_service = None

# Module-level shared SpecStore reference, set during app lifespan.
# Bound to the same MongoSpecStore instance that powers the strategy-specs
# router, the promotion manager, and the seeding flow so the cache and
# version views stay coherent across the process.
_spec_store = None
# Module-level shared StrategySpecSelector reused across requests so the
# 5-minute TTL cache survives between calls. Re-instantiated on every
# call to ``set_spec_store``.
_spec_selector = None
# Module-level shared RunTraceStore reference, set during app lifespan
# (Phase 5 / Task 68). Persists per-run trace docs to ``strategy_runs``
# so the nightly report generator consumes real run data.
_run_trace_store = None
# Module-level shared LLMCallStore reference, set during app lifespan
# (Task 90 / T2). Captures every LLM invocation made by strategy node
# executors into ``strategy_llm_calls`` for the telemetry viewer and
# admin trace UI. ``None`` disables capture silently.
_llm_call_store = None


def set_telemetry_service(service) -> None:
    """Set the module-level telemetry service reference.

    Called during app lifespan initialization so the coordinator
    can emit telemetry without needing a reference to the FastAPI app.
    """
    global _telemetry_service
    _telemetry_service = service


def set_spec_store(
    store,
    *,
    runtime_profile_store=None,
    resource_collector=None,
    evaluation_results_db=None,
    decision_store=None,
) -> None:
    """Set the shared :class:`SpecStore` used for strategy spec selection.

    Called during FastAPI lifespan startup with the same
    :class:`backend.agent.strategy.spec_store.MongoSpecStore` instance
    that backs the strategy-specs router. Resets the module-level
    selector so subsequent requests pick up the new store.

    Phase 6 / Task 78: when any of ``runtime_profile_store``,
    ``resource_collector``, or ``evaluation_results_db`` is provided,
    the module wraps the base :class:`StrategySpecSelector` in an
    :class:`AdaptiveSelector` so adaptive routing applies. Backward
    compatible — with no extras the basic selector behaviour is
    preserved.

    F8 / Task 85: optional ``decision_store`` enables persistence of
    every adaptive routing decision into the
    ``adaptive_selection_decisions`` collection for post-hoc analysis.
    Persistence is best-effort and never blocks selection.
    """
    global _spec_store, _spec_selector
    _spec_store = store
    if store is None:
        _spec_selector = None
        return
    base = StrategySpecSelector(store=store)
    if (
        runtime_profile_store is not None
        or resource_collector is not None
        or evaluation_results_db is not None
        or decision_store is not None
    ):
        _spec_selector = AdaptiveSelector(
            base_selector=base,
            runtime_profile_store=runtime_profile_store,
            resource_collector=resource_collector,
            evaluation_results_db=evaluation_results_db,
            decision_store=decision_store,
        )
    else:
        _spec_selector = base


def get_spec_selector():
    """Return the shared selector (basic or adaptive), if configured."""
    return _spec_selector


def set_run_trace_store(store) -> None:
    """Set the shared :class:`RunTraceStore` used to persist run traces.

    Called during FastAPI lifespan startup with the
    :class:`backend.agent.strategy.run_trace_store.MongoRunTraceStore`
    instance bound to ``app.state.run_trace_store``.
    """
    global _run_trace_store
    _run_trace_store = store


def get_run_trace_store():
    """Return the shared :class:`RunTraceStore`, or ``None`` if unset."""
    return _run_trace_store


def set_llm_call_store(store) -> None:
    """Set the shared :class:`LLMCallStore` used to capture LLM calls.

    Called during FastAPI lifespan startup with the
    :class:`backend.agent.strategy.llm_call_store.MongoLLMCallStore`
    instance bound to ``app.state.llm_call_store``. Mirrors the
    existing ``set_run_trace_store`` plumbing (Task 90 / T2).
    """
    global _llm_call_store
    _llm_call_store = store


def get_llm_call_store():
    """Return the shared :class:`LLMCallStore`, or ``None`` if unset."""
    return _llm_call_store


class FederatedAgent:
    """Main agent coordinator for orchestrator-worker architecture."""
    
    def __init__(
        self,
        config: Optional[AgentModeConfig] = None,
        federated_search: Optional[FederatedSearch] = None,
        strategy: Optional[BaseStrategy] = None,
        strategy_id: Optional[str] = None,
        activity_logger=None
    ):
        """Initialize the federated agent.
        
        Args:
            config: Agent configuration (mode, models, etc.)
            federated_search: FederatedSearch instance
            strategy: Direct strategy instance to use
            strategy_id: Strategy ID to load from registry
            activity_logger: ActivityLogger instance for observability (defaults to NullActivityLogger)
        """
        self.config = config or AgentModeConfig()
        self.federated_search = federated_search or get_federated_search()
        self.activity_logger = activity_logger or NullActivityLogger()
        self.req_id = self.config.request_id or uuid.uuid4().hex[:8]
        
        # Resolve strategy: explicit > config override > config selection > default
        self.strategy = self._resolve_strategy(strategy, strategy_id)
        logger.info(f"[req={self.req_id}] Using strategy: {self.strategy.metadata.id} ({self.strategy.metadata.name})")
        
        # Detect provider from model prefix (e.g., "ollama/llama3.2" → provider="ollama", model="llama3.2")
        orchestrator_model, orchestrator_provider = settings.resolve_model_provider(
            self.config.orchestrator_model, settings.orchestrator_provider
        )
        worker_model, worker_provider = settings.resolve_model_provider(
            self.config.worker_model, settings.worker_provider
        )
        
        logger.info(f"[req={self.req_id}] FederatedAgent: orchestrator={orchestrator_provider}/{orchestrator_model}, worker={worker_provider}/{worker_model}")
        logger.info(f"[req={self.req_id}] Model routing: mode={self.config.mode}, orchestrator={orchestrator_model}, worker={worker_model}, source={self.config.model_source}")
        
        # Log system state for admin observability
        self.activity_logger.log_system_state({
            "orchestrator_model": orchestrator_model,
            "orchestrator_provider": orchestrator_provider,
            "worker_model": worker_model,
            "worker_provider": worker_provider,
            "strategy": self.strategy.metadata.id,
            "mode": str(self.config.mode),
        })
        
        # Initialize components with provider configuration
        self.orchestrator = Orchestrator(
            model=orchestrator_model,
            provider=orchestrator_provider,
            strategy=self.strategy,
            activity_logger=self.activity_logger,
            req_id=self.req_id
        )
        self.worker_pool = WorkerPool(
            model=worker_model,
            provider=worker_provider,
            max_workers=self.config.parallel_workers,
            federated_search=self.federated_search,
            activity_logger=self.activity_logger,
            req_id=self.req_id
        )
        
        # Current trace
        self.trace: Optional[AgentTrace] = None
        # Business context resolved by Strategy OS (set per-request)
        self._business_context: Optional[BusinessContext] = None
        # Strategy OS execution trace (T3 / Task 91). Populated per-request
        # by _process_with_orchestrator and consumed by the response builder
        # in :mod:`backend.routers.sessions` and the SSE expansion (T4).
        self._strategy_trace: Optional[StrategyTraceResponse] = None
    
    def _resolve_strategy(
        self,
        strategy: Optional[BaseStrategy],
        strategy_id: Optional[str]
    ) -> BaseStrategy:
        """Resolve which strategy to use based on various inputs.
        
        Priority order:
        1. Explicit strategy instance
        2. Explicit strategy_id parameter
        3. Config strategy_override (direct ID)
        4. Config strategy selection (enum)
        5. Default strategy from registry
        
        Args:
            strategy: Direct strategy instance
            strategy_id: Strategy ID to load
            
        Returns:
            Resolved BaseStrategy instance
            
        Raises:
            RuntimeError: If no strategy can be resolved
        """
        errors = []
        
        # 1. Direct strategy instance
        if strategy is not None:
            logger.debug(f"Using provided strategy instance: {strategy.metadata.id}")
            return strategy
        
        # 2. Explicit strategy_id parameter
        if strategy_id:
            try:
                resolved = StrategyRegistry.get(strategy_id)
                logger.debug(f"Resolved strategy from strategy_id: {strategy_id}")
                return resolved
            except (KeyError, RuntimeError) as e:
                errors.append(f"strategy_id '{strategy_id}': {e}")
                logger.warning(f"Failed to load strategy '{strategy_id}': {e}")
        
        # 3. Config strategy_override (direct ID)
        if self.config.strategy_override:
            try:
                resolved = StrategyRegistry.get(self.config.strategy_override)
                logger.debug(f"Resolved strategy from config override: {self.config.strategy_override}")
                return resolved
            except (KeyError, RuntimeError) as e:
                errors.append(f"strategy_override '{self.config.strategy_override}': {e}")
                logger.warning(f"Failed to load strategy override '{self.config.strategy_override}': {e}")
        
        # 4. Config strategy selection (enum) - map to strategy ID
        strategy_map = {
            StrategySelection.LEGACY: "legacy",
            StrategySelection.ENHANCED: "enhanced",
            StrategySelection.SOFTWARE_DEV: "software_dev",
            StrategySelection.LEGAL: "legal",
            StrategySelection.HR: "hr",
        }
        
        if self.config.strategy != StrategySelection.AUTO:
            mapped_id = strategy_map.get(self.config.strategy)
            if mapped_id:
                try:
                    resolved = StrategyRegistry.get(mapped_id)
                    logger.debug(f"Resolved strategy from config enum: {mapped_id}")
                    return resolved
                except (KeyError, RuntimeError) as e:
                    errors.append(f"config strategy '{mapped_id}': {e}")
                    logger.warning(f"Strategy '{mapped_id}' not found, falling back to default")
        
        # 5. Default strategy from registry
        try:
            resolved = StrategyRegistry.get_default()
            logger.debug(f"Using default strategy: {resolved.metadata.id}")
            return resolved
        except (ValueError, RuntimeError) as e:
            errors.append(f"default strategy: {e}")
            logger.error(f"Failed to get default strategy: {e}")
        
        # All resolution attempts failed
        error_summary = "; ".join(errors) if errors else "Unknown error"
        raise RuntimeError(
            f"Could not resolve any strategy. Errors: {error_summary}. "
            "Ensure strategy modules are imported and at least one strategy is registered."
        )
    
    def _create_trace(
        self,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> AgentTrace:
        """Create a new trace for this execution.
        
        Args:
            user_id: User ID
            session_id: Session ID
        
        Returns:
            New AgentTrace instance
        """
        return AgentTrace(
            orchestrator_model=self.config.orchestrator_model,
            worker_model=self.config.worker_model,
            mode=self.config.mode,
            user_id=user_id,
            session_id=session_id
        )
    
    async def process(
        self,
        user_message: str,
        user_id: str,
        user_email: str,
        session_id: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        active_profile_key: Optional[str] = None,
        active_profile_database: Optional[str] = None,
        accessible_profile_keys: Optional[List[str]] = None,
        on_event: Optional[EventCallback] = None,
        language: Optional[str] = None
    ) -> Tuple[str, AgentTrace]:
        """Process a user message through the orchestrator-worker pipeline.
        
        Args:
            user_message: The user's message
            user_id: User's ID
            user_email: User's email
            session_id: Chat session ID
            conversation_history: Previous messages
            active_profile_key: Currently active profile key
            active_profile_database: Database of the active profile
            accessible_profile_keys: List of profile keys user has access to
            on_event: Optional callback for streaming events
            language: UI language code (e.g. "en", "de") for response language
        
        Returns:
            Tuple of (response text, AgentTrace)
        """
        # Reset components
        self.orchestrator.reset()
        self.worker_pool.reset()
        
        # Create trace
        self.trace = self._create_trace(user_id, session_id)
        # Reset Strategy OS trace; populated downstream when the DAG runs
        # (T3 / Task 91). Kept ``None`` for the fast path so callers can
        # distinguish "strategy did not run at all" from "strategy ran".
        self._strategy_trace = None
        
        # Determine execution mode
        use_thinking = self.config.should_use_thinking(user_message)
        
        if use_thinking:
            response = await self._process_with_orchestrator(
                user_message=user_message,
                user_id=user_id,
                user_email=user_email,
                conversation_history=conversation_history or [],
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys,
                on_event=on_event,
                language=language
            )
        else:
            response = await self._process_fast(
                user_message=user_message,
                user_id=user_id,
                user_email=user_email,
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys,
                on_event=on_event,
                language=language
            )
        
        # Finalize trace - use add methods to properly accumulate timing and token stats
        for step in self.orchestrator.steps:
            self.trace.add_orchestrator_step(step)
        for step in self.worker_pool.steps:
            self.trace.add_worker_step(step)
        self.trace.finalize()
        
        # Fire-and-forget telemetry recording (non-blocking)
        self._emit_telemetry(
            session_id=session_id or "",
            user_id=user_id,
            user_message=user_message,
            response=response,
            language=language,
            business_context=self._business_context,
        )
        
        return response, self.trace
    
    def _emit_telemetry(
        self,
        session_id: str,
        user_id: str,
        user_message: str,
        response: str,
        language: Optional[str] = None,
        business_context: Optional[BusinessContext] = None,
    ) -> None:
        """Emit telemetry record as a fire-and-forget background task.

        Extracts full trace data from orchestrator steps, worker steps,
        and LLM call records for comprehensive interaction capture.
        Non-blocking: failures are logged but never crash the request.
        """
        try:
            from backend.services.telemetry_service import TelemetryService

            telemetry: Optional[TelemetryService] = _telemetry_service
            if telemetry is None or not telemetry.enabled:
                return

            strategy_name = self.strategy.metadata.id if self.strategy else ""
            model_used = self.config.orchestrator_model
            latency_ms = int(self.trace.total_duration_ms) if self.trace else 0

            # --- Extract LLM calls from orchestrator ---
            llm_calls: list[LLMCall] = []
            # Collect orchestrator LLM call records
            for call_record in getattr(self.orchestrator, '_llm_calls', []):
                llm_calls.append(call_record)
            # Collect worker pool LLM call records (from summarize/refine tasks)
            for call_record in getattr(self.worker_pool, '_llm_calls', []):
                llm_calls.append(call_record)

            # --- Extract search operations from worker steps ---
            search_operations: list[SearchOperation] = []
            for search_record in getattr(self.worker_pool, '_search_records', []):
                search_operations.append(search_record)

            # --- Extract tool executions from worker pool ---
            tool_executions: list[ToolExecution] = []
            for task_record in getattr(self.worker_pool, '_task_records', []):
                tool_executions.append(task_record)

            # --- Extract phase metrics from orchestrator steps ---
            phase_metrics: list[PhaseMetrics] = []
            if self.trace:
                for step in self.trace.orchestrator_steps:
                    phase_metrics.append(PhaseMetrics(
                        phase=step.phase if isinstance(step.phase, str) else step.phase.value,
                        duration_ms=int(step.duration_ms),
                        tokens_used=step.tokens_used,
                        input_size=len(step.input_summary),
                        output_size=len(step.output_summary),
                        success=True,
                    ))

            # --- Token breakdown per model ---
            tokens_per_model: dict = {}
            if self.trace:
                for step in self.trace.orchestrator_steps:
                    tokens_per_model[step.model] = tokens_per_model.get(step.model, 0) + step.tokens_used
                for step in self.trace.worker_steps:
                    tokens_per_model[step.model] = tokens_per_model.get(step.model, 0) + step.tokens_used

            # --- Resolve provider names ---
            _, orchestrator_provider = settings.resolve_model_provider(
                self.config.orchestrator_model, settings.orchestrator_provider
            )
            _, worker_provider = settings.resolve_model_provider(
                self.config.worker_model, settings.worker_provider
            )

            # --- Early exit detection ---
            early_exit_triggered = False
            early_exit_confidence: Optional[float] = None
            if self.trace and self.trace.evaluation_history:
                last_eval = self.trace.evaluation_history[-1]
                if last_eval.confidence >= 0.80 and last_eval.decision == "sufficient":
                    early_exit_triggered = True
                    early_exit_confidence = last_eval.confidence

            # --- Source counts ---
            total_sources_found = 0
            deduplicated_sources = 0
            if self.trace:
                total_sources_found = sum(
                    len(s.documents) + len(s.web_links) for s in self.trace.worker_steps
                )
                deduplicated_sources = len(self.trace.all_documents) + len(self.trace.all_web_links)

            # --- Token totals ---
            prompt_tokens = 0
            response_tokens = 0
            if self.trace:
                # Estimate split from total (orchestrator heavy on input, workers balanced)
                prompt_tokens = int(self.trace.orchestrator_tokens * 0.7 + self.trace.worker_tokens * 0.5)
                response_tokens = int(self.trace.orchestrator_tokens * 0.3 + self.trace.worker_tokens * 0.5)

            # Strategy OS: Attach business context to telemetry metadata
            strategy_os_metadata: Optional[Dict[str, Any]] = None
            if business_context:
                strategy_os_metadata = {
                    "capability_id": business_context.capability_id,
                    "capability_confidence": business_context.capability_confidence,
                    "tenant_id": business_context.tenant_id,
                    "profile_key": business_context.profile_key,
                    "resolved_source_policy_from": (
                        business_context.resolved_source_policy.resolved_from
                        if business_context.resolved_source_policy
                        else None
                    ),
                }

            asyncio.create_task(
                telemetry.record_interaction(
                    session_id=session_id,
                    user_id=user_id,
                    tenant=settings.tenant_id,
                    prompt=user_message,
                    response=response,
                    agent_strategy=strategy_name,
                    model_used=model_used,
                    latency_ms=latency_ms,
                    prompt_tokens=prompt_tokens,
                    response_tokens=response_tokens,
                    language=language or "de",
                    # Full capture data
                    llm_calls=llm_calls,
                    search_operations=search_operations,
                    tool_executions=tool_executions,
                    phase_metrics=phase_metrics,
                    agent_mode=str(self.config.mode),
                    orchestrator_model=self.config.orchestrator_model,
                    worker_model=self.config.worker_model,
                    orchestrator_provider=orchestrator_provider,
                    worker_provider=worker_provider,
                    orchestrator_duration_ms=int(self.trace.orchestrator_duration_ms) if self.trace else 0,
                    worker_duration_ms=int(self.trace.worker_duration_ms) if self.trace else 0,
                    total_duration_ms=int(self.trace.total_duration_ms) if self.trace else 0,
                    tokens_per_model=tokens_per_model,
                    early_exit_triggered=early_exit_triggered,
                    early_exit_confidence=early_exit_confidence,
                    total_sources_found=total_sources_found,
                    deduplicated_sources=deduplicated_sources,
                    strategy_os_context=strategy_os_metadata,
                )
            )
        except Exception as e:
            logger.debug(f"[req={self.req_id}] Telemetry emit skipped: {e}")

    async def _process_with_orchestrator(
        self,
        user_message: str,
        user_id: str,
        user_email: str,
        conversation_history: List[Dict[str, Any]],
        active_profile_key: Optional[str],
        active_profile_database: Optional[str],
        accessible_profile_keys: Optional[List[str]],
        on_event: Optional[EventCallback] = None,
        language: Optional[str] = None
    ) -> str:
        """Process with full orchestrator-worker flow.
        
        Args:
            user_message: The user's message
            user_id: User's ID
            user_email: User's email
            conversation_history: Previous messages
            active_profile_key: Active profile key
            active_profile_database: Active profile database
            accessible_profile_keys: Accessible profile keys
            on_event: Optional callback for streaming events
        
        Returns:
            Response text
        """
        logger.info(f"[req={self.req_id}] Processing with orchestrator: '{user_message[:50]}...'")
        
        async def emit_event(event_type: str, data: Dict[str, Any]):
            """Helper to emit events safely."""
            if on_event:
                try:
                    await on_event(event_type, data)
                except Exception as e:
                    logger.error(f"[req={self.req_id}] Error emitting event {event_type}: {e}")
        
        # Strategy OS: Resolve business context (guarded by feature flag)
        business_context: Optional[BusinessContext] = None
        # Initialize the per-request Strategy OS trace (T3 / Task 91) with
        # the most pessimistic outcome; the strategy block below upgrades
        # it as more is known. Keeping the trace populated even on the
        # legacy/disabled path gives the frontend a uniform shape.
        if not settings.strategy_os_enabled:
            self._strategy_trace = build_strategy_trace(
                spec=None,
                run_result=None,
                routing_reason=ROUTING_REASON_DISABLED,
            )
        else:
            self._strategy_trace = build_strategy_trace(
                spec=None,
                run_result=None,
                routing_reason=ROUTING_REASON_LEGACY_FALLBACK,
            )
        if settings.strategy_os_enabled:
            try:
                resolver = BusinessContextResolver(tenant_id=settings.tenant_id)
                business_context = await resolver.resolve(
                    query=user_message,
                    profile_key=active_profile_key,
                    accessible_profiles=accessible_profile_keys or [],
                    matter_id=None,  # Phase 3 will add session-based matter_id
                    strategy_source_policy=None,  # Phase 2 will add strategy-specific policy
                )
                self._business_context = business_context
                logger.info(
                    f"[req={self.req_id}] BusinessContext resolved: "
                    f"capability={business_context.capability_id} "
                    f"confidence={business_context.capability_confidence:.2f}"
                )
                if not business_context.capability_id:
                    self._strategy_trace = build_strategy_trace(
                        spec=None,
                        run_result=None,
                        routing_reason=ROUTING_REASON_CAPABILITY_NOT_MATCHED,
                    )
            except Exception as e:
                logger.warning(f"[req={self.req_id}] BusinessContextResolver failed, falling back to standard search: {e}")
                business_context = None
                self._business_context = None
        
        # Phase 3 / Phase 5: Strategy spec selection based on capability.
        # The selector reads from the shared MongoSpecStore wired in the
        # FastAPI lifespan and falls back to the legacy adapter when no
        # active spec exists for the requested capability.
        strategy_spec = None
        if settings.strategy_os_enabled and business_context and business_context.capability_id:
            try:
                selector = _spec_selector
                if selector is None:
                    # No app-state spec store wired yet (e.g. CLI smoke
                    # tests): build a transient legacy-only selector so
                    # the call site still gets a deterministic result.
                    selector = StrategySpecSelector(store=None)
                strategy_spec = await selector.select(
                    capability_id=business_context.capability_id,
                    agent_mode=getattr(settings, 'agent_mode', 'auto'),
                    tenant_id=getattr(settings, 'tenant_id', 'recallhub'),
                    privacy_mode=getattr(settings, 'privacy_mode', 'standard'),
                    business_context=business_context,
                )
                if strategy_spec:
                    logger.info(f"[req={self.req_id}] Selected strategy spec: {strategy_spec.strategy_id}")
            except Exception as e:
                logger.warning(f"[req={self.req_id}] Strategy spec selection failed: {e}")
                strategy_spec = None
        
        # Strategy DAG execution (when a non-legacy spec is available)
        if strategy_spec and strategy_spec.strategy_id != "legacy_orchestrator_v1":
            try:
                registry = create_default_registry()
                runner = StrategyRunner(
                    registry=registry,
                    run_trace_store=_run_trace_store,
                    llm_call_store=_llm_call_store,
                )

                async def _on_strategy_node(
                    node_id: str,
                    node_type: str,
                    status: str,
                    duration_ms: float,
                ) -> None:
                    """Forward per-node Strategy DAG progress to the SSE stream.

                    Best-effort: any failure inside ``emit_event`` is
                    already swallowed by that helper, but we wrap a
                    second time defensively so the runner's strict
                    contract ("never raise from on_node_complete") is
                    honoured even if the indirection ever changes.
                    """
                    try:
                        await emit_event('strategy_node', {
                            'node_id': node_id,
                            'node_type': node_type,
                            'status': status,
                            'duration_ms': round(float(duration_ms or 0.0), 1),
                        })
                    except Exception as cb_exc:  # noqa: BLE001 - best effort
                        logger.debug(
                            f"[req={self.req_id}] strategy_node emit failed (non-fatal): {cb_exc}"
                        )

                result = await runner.run(
                    spec=strategy_spec,
                    context=business_context,
                    on_node_complete=_on_strategy_node,
                )
                
                if result.success and result.state and result.state.synthesis_result:
                    # Use DAG result instead of legacy path
                    synthesis_text = result.state.synthesis_result
                    logger.info(
                        f"[req={self.req_id}] Strategy DAG completed: "
                        f"{result.nodes_executed} nodes, {result.total_duration_ms}ms"
                    )
                    # Build the Strategy OS trace (T3 / Task 91) before
                    # the early return so callers can read it from
                    # ``self.strategy_trace``.
                    adaptive_scores, fast_path_eligible = (
                        await self._fetch_adaptive_scores(
                            business_context.capability_id
                            if business_context
                            else None
                        )
                    )
                    self._strategy_trace = build_strategy_trace(
                        spec=strategy_spec,
                        run_result=result,
                        routing_reason=ROUTING_REASON_ADAPTIVE,
                        adaptive_scores=adaptive_scores,
                        fast_path_eligible=fast_path_eligible,
                    )
                    return synthesis_text
            except StrategyExecutionError as e:
                logger.warning(f"[req={self.req_id}] Strategy DAG execution failed, falling back to legacy: {e}")
                self._strategy_trace = build_strategy_trace(
                    spec=strategy_spec,
                    run_result=None,
                    routing_reason=ROUTING_REASON_DAG_FAILED,
                    fallback_reason=str(e),
                )
            except Exception as e:
                logger.warning(f"[req={self.req_id}] Unexpected error in Strategy DAG, falling back to legacy: {e}")
                self._strategy_trace = build_strategy_trace(
                    spec=strategy_spec,
                    run_result=None,
                    routing_reason=ROUTING_REASON_DAG_FAILED,
                    fallback_reason=str(e),
                )
        
        # Phase 1: Analyze
        await emit_event('phase', {'phase': 'analyze', 'status': 'started'})
        self.activity_logger.log_phase("analyze", "started")
        analysis_start = time.time()
        analysis = await self.orchestrator.analyze(user_message, conversation_history)
        analyze_duration_ms = (time.time() - analysis_start) * 1000
        self.activity_logger.log_phase("analyze", "completed", duration_ms=analyze_duration_ms, details={"intent": analysis.get("intent_summary", "")})
        logger.info(f"[req={self.req_id}] Analysis: {analysis.get('intent_summary', 'unknown')}")
        # Get tokens from the last orchestrator step
        analyze_tokens = self.orchestrator.steps[-1].tokens_used if self.orchestrator.steps else 0
        await emit_event('orchestrator_step', {
            'phase': 'analyze',
            'reasoning': analysis.get('intent_summary', '')[:300],
            'duration_ms': int((time.time() - analysis_start) * 1000),
            'output': f"Complexity: {analysis.get('complexity', 'unknown')}",
            'tokens': analyze_tokens
        })
        
        # Get available sources
        available_sources = self.federated_search.get_accessible_sources(
            user_id=user_id,
            user_email=user_email,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=accessible_profile_keys
        )
        
        # Strategy OS: Execute policy-enforced search if business context resolved
        policy_search_results = None
        policy_omitted_results = None
        if business_context and business_context.resolved_source_policy:
            try:
                search_request = StrategySearchRequest(
                    query=user_message,
                    profile_key=active_profile_key,
                    resolved_policy=business_context.resolved_source_policy,
                    max_results=10,
                )
                policy_search_results, policy_omitted_results = (
                    await self.federated_search.search_with_policy(search_request)
                )
                logger.info(
                    f"[req={self.req_id}] Policy search: "
                    f"{len(policy_search_results)} valid, "
                    f"{len(policy_omitted_results)} omitted"
                )
            except Exception as e:
                logger.warning(f"[req={self.req_id}] search_with_policy failed, continuing with standard flow: {e}")
                policy_search_results = None
                policy_omitted_results = None
        
        # Phase 2: Plan
        await emit_event('phase', {'phase': 'plan', 'status': 'started'})
        self.activity_logger.log_phase("plan", "started")
        plan_start = time.time()
        plan = await self.orchestrator.plan(
            analysis=analysis,
            available_sources=[{
                "id": s.id,
                "type": s.type if isinstance(s.type, str) else s.type.value,
                "display_name": s.display_name,
                "database": s.database
            } for s in available_sources]
        )
        self.trace.initial_plan = plan
        plan_duration_ms = (time.time() - plan_start) * 1000
        self.activity_logger.log_phase("plan", "completed", duration_ms=plan_duration_ms, details={"task_count": len(plan.tasks), "strategy": plan.strategy})
        
        # Filter out disabled tools from the plan (defense in depth)
        original_count = len(plan.tasks)
        plan.tasks = ToolGate.filter_tasks(plan.tasks)
        if len(plan.tasks) < original_count:
            logger.info(f"[req={self.req_id}] ToolGate: Filtered plan from {original_count} to {len(plan.tasks)} tasks")
        
        logger.info(f"[req={self.req_id}] Plan: {len(plan.tasks)} tasks, strategy: {plan.strategy}")
        plan_tokens = self.orchestrator.steps[-1].tokens_used if self.orchestrator.steps else 0
        await emit_event('orchestrator_step', {
            'phase': 'plan',
            'reasoning': plan.strategy[:300] if plan.strategy else '',
            'duration_ms': int((time.time() - plan_start) * 1000),
            'output': f"{len(plan.tasks)} tasks planned",
            'tasks': [{'id': t.id, 'type': t.type.value if hasattr(t.type, 'value') else str(t.type), 'query': t.query[:100]} for t in plan.tasks],
            'tokens': plan_tokens
        })
        
        # Execution loop
        all_results: List[WorkerResult] = []
        iteration = 0
        empty_result_iterations = 0  # Track consecutive iterations with no results
        
        while iteration < plan.max_iterations:
            iteration += 1
            self.trace.iterations = iteration
            await emit_event('phase', {'phase': 'execute', 'iteration': iteration, 'status': 'started'})
            self.activity_logger.log_phase(f"execute_iter_{iteration}", "started")
            
            # Get tasks for this iteration
            if iteration == 1:
                tasks = plan.tasks
            else:
                tasks = evaluation.follow_up_tasks
            
            if not tasks:
                break
            
            # Execute tasks with per-task callback
            async def on_task_complete(task_id: str, result: WorkerResult, step: 'WorkerStep'):
                """Called when each task completes."""
                await emit_event('worker_step', {
                    'task_id': task_id,
                    'task_type': result.task_type.value if hasattr(result.task_type, 'value') else str(result.task_type),
                    'tool': step.tool_name if step else 'unknown',
                    'input': result.query[:200] if result.query else '',
                    'documents_count': len(result.documents_found),
                    'links_count': len(result.web_links_found),
                    'duration_ms': step.duration_ms if step else 0,
                    'success': result.success,
                    'documents': [
                        {'title': d.title, 'score': d.similarity_score, 'excerpt': d.excerpt[:150] if d.excerpt else ''}
                        for d in result.documents_found[:3]
                    ]
                })
            
            results = await self.worker_pool.execute_tasks(
                tasks=tasks,
                user_id=user_id,
                user_email=user_email,
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys,
                on_task_complete=on_task_complete if on_event else None
            )
            all_results.extend(results)
            
            # Check if this iteration found any results
            iteration_docs = sum(len(r.documents_found) for r in results)
            iteration_links = sum(len(r.web_links_found) for r in results)
            
            # Log search results summary
            exec_duration_ms = (time.time() - analysis_start) * 1000  # rough cumulative
            doc_summaries = []
            if self.config.is_admin:
                for r in results:
                    for d in r.documents_found[:3]:
                        doc_summaries.append({"title": d.title, "score": d.similarity_score})
            self.activity_logger.log_search(
                query=user_message[:200], source="federated",
                results_count=iteration_docs + iteration_links,
                duration_ms=exec_duration_ms,
                documents=doc_summaries if doc_summaries else None
            )
            self.activity_logger.log_phase(f"execute_iter_{iteration}", "completed", duration_ms=exec_duration_ms, details={"docs": iteration_docs, "links": iteration_links})
            
            # Calculate average quality score for this iteration
            avg_score = 0.0
            if iteration_docs > 0:
                all_scores = [d.similarity_score for r in results for d in r.documents_found]
                avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
            
            if iteration_docs == 0 and iteration_links == 0:
                empty_result_iterations += 1
                logger.warning(f"[req={self.req_id}] Iteration {iteration} found no results ({empty_result_iterations} consecutive empty iterations)")
                
                # Early exit if we've had 2 consecutive iterations with no results
                # No point in continuing to refine queries that aren't finding anything
                if empty_result_iterations >= 2:
                    logger.info(f"[req={self.req_id}] Exiting early: 2 consecutive iterations with no results")
                    break
            else:
                empty_result_iterations = 0  # Reset counter if we found something
                
                # Early exit if we found excellent quality results
                excellent_results = [r for r in results if r.result_quality == ResultQuality.EXCELLENT]
                good_results = [r for r in results if r.result_quality in [ResultQuality.EXCELLENT, ResultQuality.GOOD]]
                
                if len(excellent_results) >= 2 or (len(good_results) >= 3 and avg_score > 0.75):
                    logger.info(f"[req={self.req_id}] High-quality results found (avg_score={avg_score:.2f}), considering early synthesis")
                    # Skip to synthesis if we have great results on first iteration
                    if iteration == 1 and len(good_results) >= 2:
                        logger.info(f"[req={self.req_id}] Excellent first-iteration results, skipping to synthesis")
                        break
            
            # Collect sources for trace
            for r in results:
                for doc in r.documents_found:
                    if doc.id not in [d.id for d in self.trace.all_documents]:
                        self.trace.all_documents.append(doc)
                for link in r.web_links_found:
                    if link.url not in [l.url for l in self.trace.all_web_links]:
                        self.trace.all_web_links.append(link)
            
            # Phase 3: Evaluate
            await emit_event('phase', {'phase': 'evaluate', 'iteration': iteration, 'status': 'started'})
            self.activity_logger.log_phase("evaluate", "started")
            eval_start = time.time()
            evaluation = await self.orchestrator.evaluate(plan, all_results, iteration)
            eval_duration_ms = (time.time() - eval_start) * 1000
            self.activity_logger.log_phase("evaluate", "completed", duration_ms=eval_duration_ms, details={"decision": evaluation.decision, "confidence": evaluation.confidence})
            self.trace.evaluation_history.append(evaluation)
            
            logger.info(f"[req={self.req_id}] Iteration {iteration}: {evaluation.decision}, confidence: {evaluation.confidence}")
            eval_tokens = self.orchestrator.steps[-1].tokens_used if self.orchestrator.steps else 0
            await emit_event('orchestrator_step', {
                'phase': 'evaluate',
                'reasoning': evaluation.reasoning[:300] if evaluation.reasoning else '',
                'duration_ms': int((time.time() - eval_start) * 1000),
                'output': f"Decision: {evaluation.decision}, Confidence: {evaluation.confidence}",
                'decision': evaluation.decision,
                'confidence': evaluation.confidence,
                'tokens': eval_tokens
            })
            
            # High-confidence early exit
            if evaluation.decision == "sufficient" and evaluation.confidence >= 0.80:
                logger.info(f"[req={self.req_id}] High-confidence early exit (confidence={evaluation.confidence})")
                break
            
            if evaluation.decision in ["sufficient", "cannot_answer"]:
                break
            
            if not evaluation.follow_up_tasks:
                break
        
        # Phase 4: Synthesize (or return "no results" message)
        await emit_event('phase', {'phase': 'synthesize', 'status': 'started'})
        self.activity_logger.log_phase("synthesize", "started")
        synth_start = time.time()
        
        # Check if we should return a "no relevant results" response
        final_evaluation = self.trace.evaluation_history[-1] if self.trace.evaluation_history else None
        
        # Filter results by relevance - only keep results with reasonable scores
        MIN_RELEVANCE_SCORE = 0.3
        filtered_results = []
        for r in all_results:
            # Filter documents within each result
            relevant_docs = [
                doc for doc in r.documents_found 
                if getattr(doc, 'similarity_score', 0.5) >= MIN_RELEVANCE_SCORE
            ]
            if relevant_docs or r.web_links_found:
                # Create a copy with filtered documents
                filtered_result = WorkerResult(
                    task_id=r.task_id,
                    task_type=r.task_type,
                    query=r.query,
                    documents_found=relevant_docs,
                    web_links_found=r.web_links_found,
                    sources_searched=r.sources_searched,
                    error=r.error,
                    success=r.success,
                    summary=r.summary,
                    result_quality=r.result_quality
                )
                filtered_results.append(filtered_result)
        
        # If no relevant results after filtering, or cannot_answer with low confidence
        has_no_relevant_results = (
            not filtered_results or 
            all(not r.documents_found and not r.web_links_found for r in filtered_results)
        )
        
        if has_no_relevant_results and final_evaluation and final_evaluation.decision == "cannot_answer":
            # Return a clear "no results" message instead of synthesizing garbage
            sources_searched = set()
            for r in all_results:
                sources_searched.update(r.sources_searched)
            
            if language and language.lower() == "de":
                response = (
                    f"Ich habe die verfügbaren Quellen durchsucht ({', '.join(sources_searched) or 'Profil, Cloud, Persönlich'}) "
                    f"aber keine relevanten Informationen zu Ihrer Anfrage gefunden.\n\n"
                    f"**Wonach ich gesucht habe:** {plan.intent_summary if plan else user_message[:100]}\n\n"
                    f"**Vorschläge:**\n"
                    f"- Prüfen Sie, ob Dokumente zu diesem Thema aufgenommen wurden\n"
                    f"- Versuchen Sie, Ihre Frage mit anderen Schlüsselwörtern umzuformulieren\n"
                    f"- Bei der Suche nach externen Informationen kann die Websuche eingeschränkt sein"
                )
            else:
                response = (
                    f"I searched through the available sources ({', '.join(sources_searched) or 'profile, cloud, personal'}) "
                    f"but did not find relevant information about your query.\n\n"
                    f"**What I searched for:** {plan.intent_summary if plan else user_message[:100]}\n\n"
                    f"**Suggestions:**\n"
                    f"- Check if documents about this topic have been ingested\n"
                    f"- Try rephrasing your question with different keywords\n"
                    f"- If searching for external information, web search may be rate-limited"
                )
            logger.info(f"[req={self.req_id}] Returning 'no relevant results' response instead of synthesizing from irrelevant data")
        else:
            response = await self.orchestrator.synthesize(user_message, filtered_results if filtered_results else all_results, language=language)
        
        # Log synthesis result for debugging
        synth_duration_ms = (time.time() - synth_start) * 1000
        self.activity_logger.log_phase("synthesize", "completed", duration_ms=synth_duration_ms, details={"response_length": len(response) if response else 0})
        logger.info(f"[req={self.req_id}] Synthesize completed, response length: {len(response) if response else 0}")
        if not response:
            logger.error(f"[req={self.req_id}] Orchestrator synthesize returned empty response!")
        
        synth_tokens = self.orchestrator.steps[-1].tokens_used if self.orchestrator.steps else 0
        await emit_event('orchestrator_step', {
            'phase': 'synthesize',
            'reasoning': 'Generating final response from gathered information',
            'duration_ms': int((time.time() - synth_start) * 1000),
            'output': response[:200] + '...' if len(response) > 200 else response,
            'tokens': synth_tokens
        })
        
        # Contradiction detection for multi-turn sessions
        # Derive turn count and previous response from conversation history
        turn = len(conversation_history) // 2 + 1 if conversation_history else 1
        previous_response: Optional[str] = None
        if conversation_history:
            # Find the last assistant message in history
            for msg in reversed(conversation_history):
                if msg.get("role") == "assistant":
                    previous_response = msg.get("content", "")
                    break

        if settings.strategy_os_enabled and turn > 1 and previous_response:
            try:
                detector = ContradictionDetector()
                contradiction_result = await detector.detect(
                    previous_response=previous_response,
                    current_response=response,
                    session_id=self.trace.session_id or "",
                    turn=turn,
                )
                if contradiction_result.has_contradictions:
                    logger.warning(
                        f"[req={self.req_id}] Contradictions detected in session "
                        f"{self.trace.session_id} turn {turn}: "
                        f"{len(contradiction_result.contradictions)} conflicts"
                    )
                    # Store for telemetry (don't block response)
            except Exception as e:
                logger.debug(f"[req={self.req_id}] Contradiction detection skipped: {e}")

        return response
    
    async def _process_fast(
        self,
        user_message: str,
        user_id: str,
        user_email: str,
        active_profile_key: Optional[str],
        active_profile_database: Optional[str],
        accessible_profile_keys: Optional[List[str]],
        on_event: Optional[EventCallback] = None,
        language: Optional[str] = None
    ) -> str:
        """Process with fast single-model approach.
        
        For simple queries, skip orchestration and do direct search + response.
        
        Args:
            user_message: The user's message
            user_id: User's ID
            user_email: User's email
            active_profile_key: Active profile key
            active_profile_database: Active profile database
            accessible_profile_keys: Accessible profile keys
            on_event: Optional callback for streaming events
        
        Returns:
            Response text
        """
        logger.info(f"[req={self.req_id}] Processing fast: '{user_message[:50]}...'")
        
        async def emit_event(event_type: str, data: Dict[str, Any]):
            """Helper to emit events safely."""
            if on_event:
                try:
                    await on_event(event_type, data)
                except Exception as e:
                    logger.error(f"[req={self.req_id}] Error emitting event {event_type}: {e}")
        
        await emit_event('phase', {'phase': 'fast_search', 'status': 'started'})
        self.activity_logger.log_phase("fast_search", "started")
        
        # Create simple search tasks
        tasks = [
            TaskDefinition(
                id="search_all",
                type=TaskType.SEARCH_ALL,
                query=user_message,
                max_results=10
            )
        ]
        
        # Add web search for questions that might need external info
        # Only if web_search is enabled for this tenant
        if ToolGate.is_enabled("web_search"):
            question_words = ["what is", "who is", "how to", "why", "when", "where"]
            if any(w in user_message.lower() for w in question_words):
                tasks.append(TaskDefinition(
                    id="web_search",
                    type=TaskType.WEB_SEARCH,
                    query=user_message,
                    max_results=5
                ))
        
        # Execute tasks with callback
        async def on_task_complete(task_id: str, result: WorkerResult, step: 'WorkerStep'):
            """Called when each task completes."""
            await emit_event('worker_step', {
                'task_id': task_id,
                'task_type': result.task_type.value if hasattr(result.task_type, 'value') else str(result.task_type),
                'tool': step.tool_name if step else 'unknown',
                'input': result.query[:200] if result.query else '',
                'documents_count': len(result.documents_found),
                'links_count': len(result.web_links_found),
                'duration_ms': step.duration_ms if step else 0,
                'success': result.success,
                'documents': [
                    {'title': d.title, 'score': d.similarity_score, 'excerpt': d.excerpt[:150] if d.excerpt else ''}
                    for d in result.documents_found[:3]
                ]
            })
        
        results = await self.worker_pool.execute_tasks(
            tasks=tasks,
            user_id=user_id,
            user_email=user_email,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=accessible_profile_keys,
            on_task_complete=on_task_complete if on_event else None
        )
        
        # Collect sources for trace
        for r in results:
            for doc in r.documents_found:
                if doc.id not in [d.id for d in self.trace.all_documents]:
                    self.trace.all_documents.append(doc)
            for link in r.web_links_found:
                if link.url not in [l.url for l in self.trace.all_web_links]:
                    self.trace.all_web_links.append(link)
        
        self.trace.iterations = 1
        
        # Log fast search completion
        fast_docs = sum(len(r.documents_found) for r in results)
        fast_links = sum(len(r.web_links_found) for r in results)
        self.activity_logger.log_search(
            query=user_message[:200], source="fast_search",
            results_count=fast_docs + fast_links, duration_ms=0
        )
        self.activity_logger.log_phase("fast_search", "completed", details={"docs": fast_docs, "links": fast_links})
        
        # Generate response using fast model
        await emit_event('phase', {'phase': 'synthesize', 'status': 'started'})
        self.activity_logger.log_phase("fast_synthesize", "started")
        synth_start = time.time()
        response = await self._generate_fast_response(user_message, results, language=language)
        fast_synth_duration_ms = (time.time() - synth_start) * 1000
        self.activity_logger.log_phase("fast_synthesize", "completed", duration_ms=fast_synth_duration_ms, details={"response_length": len(response) if response else 0})
        await emit_event('orchestrator_step', {
            'phase': 'synthesize',
            'reasoning': 'Fast response generation from search results',
            'duration_ms': int((time.time() - synth_start) * 1000),
            'output': response[:200] + '...' if len(response) > 200 else response
        })
        
        return response
    
    def _get_worker_model_string(self) -> str:
        """Get the worker model string in LiteLLM format."""
        # Resolve provider from model prefix first
        model, provider = settings.resolve_model_provider(
            self.config.worker_model, settings.worker_provider
        )
        provider = provider.lower()
        
        if provider == "openai":
            return model
        elif provider == "google" or provider == "gemini":
            if not model.startswith("gemini/"):
                return f"gemini/{model}"
            return model
        elif provider == "anthropic" or provider == "claude":
            if not model.startswith("anthropic/"):
                return f"anthropic/{model}"
            return model
        elif provider == "ollama":
            if not model.startswith("ollama/"):
                return f"ollama/{model}"
            return model
        return model
    
    async def _generate_fast_response(
        self,
        user_message: str,
        results: List[WorkerResult],
        language: Optional[str] = None
    ) -> str:
        """Generate response using fast model.
        
        Args:
            user_message: User's message
            results: Search results
            language: UI language code for response language
        
        Returns:
            Response text
        """
        from litellm import acompletion
        from backend.core.config import settings
        
        # Compile context from results
        context_parts = []
        for r in results:
            for doc in r.documents_found[:5]:
                context_parts.append(f"[Document: {doc.title}]\n{doc.full_content or doc.excerpt}")
            for link in r.web_links_found[:3]:
                context_parts.append(f"[Web: {link.title}]\n{link.excerpt}")
        
        context = "\n\n".join(context_parts) if context_parts else "No relevant information found."
        
        # Get prompt from database/defaults
        prompt_template = get_agent_prompt_sync("agent_fast_response")
        prompt = prompt_template.format(
            user_message=user_message,
            context=context
        )
        
        # Inject language instruction if non-English language is specified
        if language and language.lower() != "en":
            lang_names = {"de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "nl": "Dutch"}
            lang_name = lang_names.get(language.lower(), language)
            prompt += f"\n\n**IMPORTANT: You MUST write your entire response in {lang_name}. The user's interface is set to {lang_name} and the question was asked in {lang_name}. Respond ONLY in {lang_name}.**"
        
        try:
            # Use the correct model string and API key based on provider
            model_string = self._get_worker_model_string()
            api_key = settings.get_worker_api_key()
            
            # Handle newer OpenAI models that require max_completion_tokens
            llm_params = {
                "model": model_string,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "api_key": api_key,
            }
            
            # Set api_base for Ollama models so LiteLLM routes to the correct host
            if "ollama/" in model_string:
                llm_params["api_base"] = settings.ollama_base_url
                llm_params["timeout"] = settings.agent_llm_request_timeout
            
            # Check if this is a newer OpenAI model
            if "gpt-5" in model_string.lower() or "gpt-4o" in model_string.lower():
                llm_params["max_completion_tokens"] = 1500
            else:
                llm_params["max_tokens"] = 1500
            
            response = await acompletion(**llm_params)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"[req={self.req_id}] Fast response generation failed: {e}")
            self.activity_logger.log_error(str(e), context={"phase": "fast_response"})
            return f"I encountered an error while generating a response: {str(e)}"
    
    async def cleanup(self):
        """Cleanup resources."""
        await self.worker_pool.cleanup()

    async def _fetch_adaptive_scores(
        self,
        capability_id: Optional[str],
    ) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
        """Read the most recent :class:`AdaptiveDecisionDoc` for ``capability_id``.

        Used to enrich :class:`StrategyTraceResponse` with the per-candidate
        score breakdown that the :class:`AdaptiveSelector` produced for
        this request. Reads from the ``decision_store`` attached to the
        module-level selector — a query, not a transient cache, since
        the selector returns only the chosen :class:`StrategySpec` and
        the brief constrains us not to widen its return type.

        Best-effort: any exception is logged at DEBUG and the call
        returns ``(None, False)`` so the trace is still emitted.

        Args:
            capability_id: Capability the spec was selected for. ``None``
                returns ``(None, False)`` immediately.

        Returns:
            ``(candidate_scores, fast_path_eligible)`` from the most
            recent persisted decision, or ``(None, False)`` when no
            decision is available.
        """
        if not capability_id:
            return None, False
        selector = _spec_selector
        if not isinstance(selector, AdaptiveSelector):
            return None, False
        store = getattr(selector, "decision_store", None)
        if store is None:
            return None, False
        try:
            recent = await store.list_recent(
                capability_id=capability_id,
                limit=1,
            )
        except Exception as exc:  # noqa: BLE001 - trace is best-effort
            logger.debug(
                f"[req={self.req_id}] adaptive decision lookup skipped: {exc}"
            )
            return None, False
        if not recent:
            return None, False
        latest = recent[0]
        return list(latest.candidate_scores), bool(latest.fast_path_eligible)

    @property
    def strategy_trace(self) -> Optional[StrategyTraceResponse]:
        """Return the Strategy OS trace from the most recent request.

        Populated by :meth:`_process_with_orchestrator` when the strategy
        path runs (or attempts to run); ``None`` for the fast path or
        before :meth:`process` has been invoked.

        Consumers (response builder in ``backend.routers.sessions`` and
        the SSE expansion in T4) MUST treat the trace as read-only.
        """
        return self._strategy_trace


# Factory function
def create_federated_agent(
    mode: AgentMode = AgentMode.AUTO,
    orchestrator_model: Optional[str] = None,
    worker_model: Optional[str] = None,
    max_iterations: int = 3,
    strategy: Optional[StrategySelection] = None,
    strategy_id: Optional[str] = None
) -> FederatedAgent:
    """Create a FederatedAgent with the specified configuration.
    
    Args:
        mode: Agent mode (auto, thinking, fast)
        orchestrator_model: Model for orchestration
        worker_model: Model for worker tasks
        max_iterations: Maximum iterations
        strategy: Strategy selection enum
        strategy_id: Direct strategy ID override
    
    Returns:
        Configured FederatedAgent
    """
    config = AgentModeConfig(
        mode=mode,
        orchestrator_model=orchestrator_model or settings.orchestrator_model,
        worker_model=worker_model or settings.worker_model,
        max_iterations=max_iterations,
        strategy=strategy or StrategySelection.AUTO,
        strategy_override=strategy_id
    )
    
    return FederatedAgent(config=config)
