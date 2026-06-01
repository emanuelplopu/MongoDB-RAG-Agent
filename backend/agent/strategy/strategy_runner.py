"""
Strategy OS DAG execution engine.

Executes StrategySpec graphs using level-synchronous parallelism:
- Nodes at the same topological level run concurrently (asyncio.gather)
- State accumulation is append-only between levels
- Budget enforcement halts execution on hard limit breach
- Conditional edges are evaluated at level boundaries
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Any, Awaitable, Callable

from backend.agent.strategy.models import (
    StrategySpec,
    StrategyRunState,
    StrategyRunResult,
    StrategyNode,
    StrategyBudgets,
    NodeOutput,
    BusinessContext,
    RetrievedChunk,
    EvidenceCard,
    SynthesisResult,
    ValidationResult,
)
from backend.agent.strategy.graph_compiler import (
    compile_graph,
    CompiledGraph,
    GraphCompilationError,
)
from backend.agent.strategy.condition_evaluator import (
    restricted_eval,
    ConditionEvaluationError,
)
from backend.agent.strategy.nodes.registry import NodeRegistry
from backend.agent.strategy.checkpoint_manager import CheckpointManager
from backend.agent.strategy.run_trace_store import (
    MongoRunTraceStore,
    RunTraceDoc,
    RunTraceStore,
)
from backend.agent.strategy.llm_call_store import LLMCallStore
from backend.agent.strategy.llm_helper import (
    LLMCaptureContext,
    reset_capture_context,
    set_capture_context,
)

logger = logging.getLogger(__name__)

__all__ = [
    "StrategyRunner",
    "StrategyExecutionError",
    "StateViolationError",
    "NodeCompleteCallback",
]


# (node_id, node_type, status, duration_ms)
NodeCompleteCallback = Callable[[str, str, str, float], Awaitable[None]]


# ═══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyExecutionError(Exception):
    """Raised when strategy execution encounters an unrecoverable error."""

    pass


class StateViolationError(Exception):
    """Raised when append-only state invariant is violated."""

    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_NODE_TIMEOUT_S = 300  # 5 minutes
_TERMINAL_STATUSES = frozenset({"error", "timed_out"})
_NON_SUCCESS_STATUSES = frozenset({"error", "timed_out", "skipped", "empty"})


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Runner
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyRunner:
    """
    Core DAG execution engine for the Strategy OS.

    Executes StrategySpec graphs using level-synchronous parallelism model:
    1. Compile graph into topological levels
    2. Execute each level: all nodes at level N run in parallel
    3. Wait for all nodes at level N to complete
    4. Merge outputs into state (append-only)
    5. Evaluate conditions for level N+1
    6. Check budget constraints
    7. Repeat until terminal level or halt
    """

    def __init__(
        self,
        registry: NodeRegistry,
        model_registry=None,
        db=None,
        llm_helper=None,
        checkpoint_manager: Optional[CheckpointManager] = None,
        run_trace_store: Optional[RunTraceStore] = None,
        llm_call_store: Optional[LLMCallStore] = None,
    ):
        """
        Initialize the strategy runner.

        Args:
            registry: NodeRegistry with registered node executors
            model_registry: Optional ModelRoleRegistry for model resolution
            db: Optional MongoDB client for checkpoint persistence (Phase 2b)
            llm_helper: Optional NodeLLMHelper instance (stored for
                        reference; nodes receive it via the registry).
            checkpoint_manager: Optional CheckpointManager for run resilience.
                If not provided but ``db`` is, a default one is created.
            run_trace_store: Optional :class:`RunTraceStore` used to persist
                per-run trace docs to ``strategy_runs`` for the nightly
                report generator (Phase 5 / Task 68). When ``None`` and
                ``db`` is provided, a default :class:`MongoRunTraceStore`
                is instantiated. When both are ``None``, persistence is
                skipped silently.
            llm_call_store: Optional :class:`LLMCallStore` used to capture
                every LLM invocation made by node executors via the
                shared :class:`NodeLLMHelper`. Plumbed through a
                per-node :class:`LLMCaptureContext` so node executors
                require zero code changes (Task 90 / T2). When
                ``None`` LLM-call capture is silently disabled.
        """
        self.registry = registry
        self.model_registry = model_registry
        self.db = db
        self.llm_helper = llm_helper
        self.checkpoint_manager = checkpoint_manager
        if self.checkpoint_manager is None and db is not None:
            self.checkpoint_manager = CheckpointManager(db=db)
        self.run_trace_store = run_trace_store
        if self.run_trace_store is None and db is not None:
            try:
                self.run_trace_store = MongoRunTraceStore(db)
            except Exception as e:  # noqa: BLE001 - best effort
                logger.debug(
                    "Default MongoRunTraceStore could not be constructed: %s", e
                )
                self.run_trace_store = None
        self.llm_call_store = llm_call_store
        self._cancelled = False
        # Per-run callback wired via run(); cleared after each run.
        self._on_node_complete: Optional[NodeCompleteCallback] = None

    async def run(
        self,
        spec: StrategySpec,
        context: BusinessContext,
        query: str = "",
        on_node_complete: Optional[NodeCompleteCallback] = None,
    ) -> StrategyRunResult:
        """
        Main entry point: execute a full strategy DAG.

        Args:
            spec: The strategy specification with graph, budgets, policies
            context: Resolved business context (capability, policy, language, etc.)
            query: The user query to process
            on_node_complete: Optional best-effort async callback invoked
                after each node finishes (post state-merge). Receives
                ``(node_id, node_type, status, duration_ms)``. Exceptions
                raised by the callback are swallowed so streaming
                telemetry can never fail the run (Task 92 / T4).

        Returns:
            StrategyRunResult with success/failure, state, timing, and halt_reason
        """
        run_start = time.perf_counter()
        started_at = datetime.now(timezone.utc)
        halt_reason: Optional[str] = None
        run_exception: Optional[BaseException] = None
        # Stash the callback for _execute_levels; cleared in the finally
        # block so the runner remains reusable across runs.
        self._on_node_complete = on_node_complete

        try:
            # 1. Initialize state
            state = self._init_state(spec, context, query)
            logger.info(
                "Starting strategy run: strategy_id=%s, run_id=%s",
                spec.strategy_id,
                state.run_id,
            )

            # 2. Compile and validate graph
            compiled = self._compile_and_validate(spec.graph)

            # 3. Execute level-by-level
            halt_reason = await self._execute_levels(compiled, state, spec.budgets)

            # 4. Update final elapsed time
            state.elapsed_ms = (time.perf_counter() - run_start) * 1000

            # 5. On successful completion, clean up any persisted checkpoint
            if self.checkpoint_manager and halt_reason is None:
                try:
                    await self.checkpoint_manager.delete_checkpoint(state.run_id)
                except Exception as e:
                    logger.debug(
                        "Checkpoint delete failed (non-fatal) for run %s: %s",
                        state.run_id,
                        e,
                    )

        except StrategyExecutionError as e:
            logger.error("Strategy execution error: %s", e)
            run_exception = e
            if "state" not in locals():
                state = StrategyRunState(
                    query=query,
                    metadata={"strategy_id": spec.strategy_id, "error": str(e)},
                )
            state.elapsed_ms = (time.perf_counter() - run_start) * 1000
            halt_reason = f"execution_error: {e}"

        except Exception as e:
            logger.exception("Unexpected error during strategy execution")
            run_exception = e
            if "state" not in locals():
                state = StrategyRunState(
                    query=query,
                    metadata={"strategy_id": spec.strategy_id, "error": str(e)},
                )
            state.elapsed_ms = (time.perf_counter() - run_start) * 1000
            halt_reason = f"unexpected_error: {type(e).__name__}: {e}"

        # Always clear the per-run callback before returning so the
        # runner instance can be reused safely across requests.
        self._on_node_complete = None

        result = self._finalize(state, halt_reason, spec)
        completed_at = datetime.now(timezone.utc)

        # Best-effort trace persistence — never fails the user-visible run.
        await self._persist_trace(
            result,
            spec=spec,
            started_at=started_at,
            completed_at=completed_at,
            halt_reason=halt_reason,
            had_exception=run_exception is not None,
        )

        return result

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancelled = True

    # ═══════════════════════════════════════════════════════════════════════════
    # Trace Persistence (Phase 5 / Task 68)
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _map_run_status(
        *, halt_reason: Optional[str], had_exception: bool
    ) -> str:
        """Map runner outcome to the canonical persisted status.

        Aligned with
        :data:`backend.agent.strategy.report_generator.EXPECTED_RUN_DOC`:
        ``success | failed | timed_out | cancelled``.
        """
        if halt_reason is None and not had_exception:
            return "success"
        if had_exception:
            return "failed"
        reason = (halt_reason or "").lower()
        if reason.startswith("execution_error") or reason.startswith(
            "unexpected_error"
        ) or reason.startswith("state_violation"):
            return "failed"
        if (
            reason.startswith("latency_hard_limit")
            or "timed_out" in reason
            or "timeout" in reason
        ):
            return "timed_out"
        # Budget halts, node halts, explicit cancellation -> cancelled.
        return "cancelled"

    async def _persist_trace(
        self,
        result: StrategyRunResult,
        *,
        spec: StrategySpec,
        started_at: datetime,
        completed_at: datetime,
        halt_reason: Optional[str],
        had_exception: bool,
    ) -> None:
        """Persist a per-run trace doc to the configured store.

        Best-effort: errors are logged and swallowed so a persistence
        failure cannot fail an otherwise-successful user-visible run.
        """
        if self.run_trace_store is None:
            return
        try:
            status = self._map_run_status(
                halt_reason=halt_reason, had_exception=had_exception
            )
            state = result.state
            metadata = state.metadata or {}
            node_outputs_payload = [
                {
                    "node_id": out.node_id,
                    "node_type": out.node_type,
                    "status": out.status,
                    "duration_ms": float(out.duration_ms or 0.0),
                    "error": out.error_message,
                }
                for out in state.node_outputs.values()
            ]
            trace_doc = RunTraceDoc(
                trace_id=state.trace_id,
                run_id=state.run_id,
                strategy_id=result.strategy_id or spec.strategy_id,
                strategy_version=getattr(spec, "version", None),
                capability_id=metadata.get("capability_id"),
                tenant_id=metadata.get("tenant_id"),
                status=status,  # type: ignore[arg-type]
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=float(result.total_duration_ms or 0.0),
                node_outputs=node_outputs_payload,
                halt_reason=halt_reason,
            )
            await self.run_trace_store.save(trace_doc)
        except Exception as e:  # noqa: BLE001 - best effort
            logger.warning(
                "Run-trace persistence failed (non-fatal) for strategy_id=%s: %s",
                spec.strategy_id,
                e,
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # State Initialization
    # ═══════════════════════════════════════════════════════════════════════════

    def _init_state(
        self,
        spec: StrategySpec,
        context: BusinessContext,
        query: str = "",
    ) -> StrategyRunState:
        """Create fresh StrategyRunState for a new execution run."""
        return StrategyRunState(
            run_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            query=query,
            business_context=None,
            retrieved_chunks=[],
            evidence_cards=[],
            synthesis_result=None,
            validation_results=[],
            node_outputs={},
            metadata={
                "strategy_id": spec.strategy_id,
                "spec_hash": spec.spec_hash,
                "capability_id": context.capability_id,
                "tenant_id": context.tenant_id,
                "profile_key": context.profile_key,
            },
            elapsed_ms=0.0,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Graph Compilation
    # ═══════════════════════════════════════════════════════════════════════════

    def _compile_and_validate(self, graph) -> CompiledGraph:
        """Compile and validate the strategy graph.

        Raises:
            StrategyExecutionError: If compilation fails.
        """
        try:
            compiled = compile_graph(graph)
            return compiled
        except GraphCompilationError as e:
            raise StrategyExecutionError(
                f"Graph compilation failed: {e}"
            ) from e

    # ═══════════════════════════════════════════════════════════════════════════
    # Level Execution Loop
    # ═══════════════════════════════════════════════════════════════════════════

    async def _execute_levels(
        self,
        compiled: CompiledGraph,
        state: StrategyRunState,
        budgets: StrategyBudgets,
    ) -> Optional[str]:
        """Execute all topological levels sequentially.

        Within each level, reachable nodes execute in parallel.

        Returns:
            halt_reason string if execution was halted, None on successful completion.
        """
        for level_idx, level_node_ids in enumerate(compiled.levels):
            # Check cancellation
            if self._cancelled:
                state.cancelled = True
                logger.info("Strategy run cancelled at level %d", level_idx)
                return "cancelled"

            # Check budget BEFORE starting this level
            budget_halt = self._check_budget(state, budgets)
            if budget_halt:
                logger.warning(
                    "Budget halt at level %d: %s", level_idx, budget_halt
                )
                return budget_halt

            # Determine which nodes in this level are reachable
            reachable_nodes: list[StrategyNode] = []
            for node_id in level_node_ids:
                if self._is_node_reachable(node_id, compiled, state):
                    node = compiled.node_map[node_id]
                    reachable_nodes.append(node)
                else:
                    logger.debug(
                        "Node '%s' at level %d is not reachable, skipping",
                        node_id,
                        level_idx,
                    )

            if not reachable_nodes:
                logger.debug("No reachable nodes at level %d, continuing", level_idx)
                continue

            # Execute reachable nodes in parallel
            level_start = time.perf_counter()
            logger.info(
                "Executing level %d: %d node(s) [%s]",
                level_idx,
                len(reachable_nodes),
                ", ".join(n.node_id for n in reachable_nodes),
            )

            outputs = await asyncio.gather(
                *[self._execute_single_node(node, state) for node in reachable_nodes],
                return_exceptions=False,
            )

            # Merge outputs and check for halt conditions
            for output in outputs:
                try:
                    self._merge_node_output(state, output)
                except StateViolationError as e:
                    logger.error("State violation: %s", e)
                    return f"state_violation: {e}"

                # Best-effort per-node telemetry callback. Fired after
                # the merge so the SSE stream can surface progress.
                # Skip ``pending`` outputs (defensive — should never
                # happen here since execute_single_node always returns
                # a terminal status). Never let callback failures
                # affect the run.
                if (
                    self._on_node_complete is not None
                    and output.status != "pending"
                ):
                    try:
                        await self._on_node_complete(
                            output.node_id,
                            output.node_type,
                            output.status,
                            float(output.duration_ms or 0.0),
                        )
                    except Exception as cb_exc:  # noqa: BLE001 - best effort
                        logger.debug(
                            "on_node_complete callback failed for node %s (non-fatal): %s",
                            output.node_id,
                            cb_exc,
                        )

                # Check on_error="halt" policy
                if output.status in _TERMINAL_STATUSES:
                    node = compiled.node_map.get(output.node_id)
                    if node and node.on_error == "halt" and not node.optional:
                        logger.warning(
                            "Node '%s' failed with on_error=halt, stopping execution",
                            output.node_id,
                        )
                        return f"node_halt: {output.node_id} ({output.error_message})"

            # Update elapsed time
            level_duration = (time.perf_counter() - level_start) * 1000
            state.elapsed_ms += level_duration

            # Persist checkpoint after a successful level completion
            if self.checkpoint_manager:
                try:
                    await self.checkpoint_manager.save_checkpoint(state, level_idx)
                except Exception as e:
                    logger.debug(
                        "Checkpoint save failed (non-fatal) at level %d: %s",
                        level_idx,
                        e,
                    )

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Single Node Execution
    # ═══════════════════════════════════════════════════════════════════════════

    async def _execute_single_node(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """Execute a single node with timeout handling.

        When :attr:`llm_call_store` is configured, a fresh
        :class:`LLMCaptureContext` is pushed for the duration of the
        node so any LLM invocation routed through
        :class:`NodeLLMHelper` is persisted automatically. Captured
        ``call_id`` values are appended to ``state.llm_call_ids`` so
        the run trace cross-references every prompt/response.

        Returns:
            NodeOutput with appropriate status (success, error, timed_out, skipped).
        """
        timeout_s = (
            node.timeout_ms / 1000.0 if node.timeout_ms else _DEFAULT_NODE_TIMEOUT_S
        )

        capture: Optional[LLMCaptureContext] = None
        capture_token = None
        if self.llm_call_store is not None:
            capture = LLMCaptureContext(
                trace_id=state.trace_id,
                node_id=node.node_id,
                node_type=node.node_type,
                store=self.llm_call_store,
                call_ids=[],
            )
            try:
                capture_token = set_capture_context(capture)
            except Exception as exc:  # noqa: BLE001 - never break the run
                logger.debug(
                    "set_capture_context failed for node %s (non-fatal): %s",
                    node.node_id,
                    exc,
                )
                capture = None
                capture_token = None

        try:
            output = await asyncio.wait_for(
                self._execute_node_with_retry(node, state),
                timeout=timeout_s,
            )
            return output

        except asyncio.TimeoutError:
            logger.warning(
                "Node '%s' timed out after %.1fs", node.node_id, timeout_s
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="timed_out",
                duration_ms=timeout_s * 1000,
                error_message=f"Node timed out after {timeout_s:.1f}s",
                timed_out=True,
                tokens_used=0,
                retry_count=0,
            )

        except Exception as e:
            logger.error(
                "Unexpected error in node '%s': %s", node.node_id, e
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="error",
                error_message=f"{type(e).__name__}: {e}",
                tokens_used=0,
                retry_count=0,
            )
        finally:
            if capture_token is not None:
                try:
                    reset_capture_context(capture_token)
                except Exception as exc:  # noqa: BLE001 - never break the run
                    logger.debug(
                        "reset_capture_context failed for node %s (non-fatal): %s",
                        node.node_id,
                        exc,
                    )
            if capture is not None and capture.call_ids:
                try:
                    state.llm_call_ids.extend(capture.call_ids)
                except Exception as exc:  # noqa: BLE001 - never break the run
                    logger.debug(
                        "Could not append captured llm_call_ids for node %s: %s",
                        node.node_id,
                        exc,
                    )

    async def _execute_node_with_retry(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """Execute a node with retry logic based on node.retry_config.

        Handles:
        - Retries on error up to max_retries
        - on_empty policy (skip or error)
        - on_error policy (skip, halt, retry fallback to skip)
        """
        max_retries = 0
        if node.retry_config:
            max_retries = node.retry_config.get("max_retries", 0)

        retry_count = 0
        last_output: Optional[NodeOutput] = None

        for attempt in range(max_retries + 1):
            output = await self.registry.execute(node, state)
            output.retry_count = retry_count

            if output.status == "success":
                return output

            if output.status == "empty":
                # Apply on_empty policy
                if node.on_empty == "skip":
                    output.status = "skipped"
                    logger.info(
                        "Node '%s' returned empty, skipping (on_empty=skip)",
                        node.node_id,
                    )
                    return output
                else:
                    # on_empty == "error" — treat as error, allow retry
                    output.status = "error"
                    output.error_message = output.error_message or "Node returned empty result"

            # Status is "error" or "timed_out" at this point
            last_output = output

            if attempt < max_retries:
                retry_count += 1
                retry_delay = node.retry_config.get("delay_ms", 500) / 1000.0 if node.retry_config else 0.5
                logger.info(
                    "Node '%s' failed (attempt %d/%d), retrying in %.1fs: %s",
                    node.node_id,
                    attempt + 1,
                    max_retries + 1,
                    retry_delay,
                    output.error_message,
                )
                await asyncio.sleep(retry_delay)

        # All retries exhausted — apply on_error policy
        assert last_output is not None
        last_output.retry_count = retry_count

        if node.on_error == "skip" or node.optional:
            last_output.status = "skipped"
            logger.info(
                "Node '%s' failed after %d retries, skipping (on_error=%s, optional=%s)",
                node.node_id,
                retry_count,
                node.on_error,
                node.optional,
            )
            return last_output

        # on_error == "halt" or "retry" (retry already exhausted, fall through)
        # Return the error output; the caller (_execute_levels) checks on_error
        logger.warning(
            "Node '%s' failed after %d retries: %s",
            node.node_id,
            retry_count,
            last_output.error_message,
        )
        return last_output

    # ═══════════════════════════════════════════════════════════════════════════
    # Reachability (Conditional Edge Evaluation)
    # ═══════════════════════════════════════════════════════════════════════════

    def _is_node_reachable(
        self,
        node_id: str,
        compiled: CompiledGraph,
        state: StrategyRunState,
    ) -> bool:
        """Determine if a node is reachable given current state.

        A node is reachable if:
        - It is the entry node (always reachable), OR
        - At least one incoming path is satisfied:
          - Source node completed (not in terminal failure unless optional)
          - Edge condition evaluates to True (or no condition)

        Uses OR semantics: any single satisfied incoming path makes node reachable.
        """
        # Entry node is always reachable
        if node_id == compiled.entry_node:
            return True

        predecessors = compiled.predecessors(node_id)
        if not predecessors:
            # Node with no predecessors but not entry — still reachable (orphan at level 0)
            return True

        # OR semantics: at least one incoming path must be satisfied
        for pred_id in predecessors:
            # Check if predecessor completed
            pred_output = state.node_outputs.get(pred_id)

            if pred_output is None:
                # Predecessor hasn't run yet — this path is not satisfied
                continue

            # Check if predecessor is in a failed state
            pred_node = compiled.node_map.get(pred_id)
            if pred_output.status in _TERMINAL_STATUSES:
                # Failed predecessor — only satisfies path if it's optional
                if pred_node and not pred_node.optional:
                    continue

            # Check edge condition
            condition = compiled.get_condition(pred_id, node_id)
            if condition is not None:
                try:
                    ctx = self._build_condition_context(state)
                    result = restricted_eval(condition, ctx)
                    if not result:
                        # Condition evaluated to False
                        continue
                except ConditionEvaluationError as e:
                    logger.warning(
                        "Condition evaluation failed for edge %s -> %s: %s. "
                        "Treating as blocked.",
                        pred_id,
                        node_id,
                        e,
                    )
                    continue

            # This path is satisfied
            return True

        return False

    def _build_condition_context(self, state: StrategyRunState) -> dict[str, Any]:
        """Build the context dict for edge condition evaluation.

        Provides commonly-needed state metrics that conditions can reference.
        """
        return {
            "chunks_count": len(state.retrieved_chunks),
            "has_evidence": len(state.evidence_cards) > 0,
            "evidence_count": len(state.evidence_cards),
            "has_synthesis": state.synthesis_result is not None,
            "validation_passed": (
                all(v.passed for v in state.validation_results)
                if state.validation_results
                else True
            ),
            "elapsed_ms": state.elapsed_ms,
            "nodes_completed": len(state.node_outputs),
            "has_chunks": len(state.retrieved_chunks) > 0,
            "query": state.query,
            "normalized_query": state.normalized_query,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # State Merging (Append-Only)
    # ═══════════════════════════════════════════════════════════════════════════

    def _merge_node_output(self, state: StrategyRunState, output: NodeOutput) -> None:
        """Merge a NodeOutput into the run state.

        CRITICAL: Enforces append-only invariant — duplicate node_id raises
        StateViolationError.

        Merges output_data into the appropriate state field based on node_type.
        Failed nodes (non-success status) are recorded but their data is not merged.
        """
        # Append-only invariant
        if output.node_id in state.node_outputs:
            raise StateViolationError(
                f"Node '{output.node_id}' already has output in state. "
                f"Append-only invariant violated."
            )

        # Always record the output
        state.node_outputs[output.node_id] = output

        # Don't merge data from failed nodes
        if output.status != "success":
            return

        # No data to merge
        if output.output_data is None:
            return

        # Route output_data to the appropriate state field
        self._route_output_to_state(state, output)

    def _route_output_to_state(
        self, state: StrategyRunState, output: NodeOutput
    ) -> None:
        """Route successful node output data to the correct state field."""
        node_type = output.node_type
        data = output.output_data

        if node_type == "normalize_query":
            if isinstance(data, str):
                state.normalized_query = data
            elif isinstance(data, dict):
                state.normalized_query = data.get("normalized_query", str(data))
            state.metadata["normalized_query"] = state.normalized_query

        elif node_type in ("retrieve", "rerank", "dedupe_versions", "boilerplate_filter"):
            if isinstance(data, list):
                state.retrieved_chunks = [
                    RetrievedChunk(**c) if isinstance(c, dict) else c
                    for c in data
                ]

        elif node_type == "evidence_cards":
            if isinstance(data, list):
                state.evidence_cards = [
                    EvidenceCard(**c) if isinstance(c, dict) else c
                    for c in data
                ]

        elif node_type in ("synthesize", "refine", "compare_candidates"):
            if isinstance(data, dict):
                state.synthesis_result = SynthesisResult(**data)
            elif isinstance(data, SynthesisResult):
                state.synthesis_result = data

        elif node_type in ("validate_citations", "validate_contract", "judge_quality"):
            if isinstance(data, dict):
                state.validation_results.append(ValidationResult(**data))
            elif isinstance(data, ValidationResult):
                state.validation_results.append(data)

        elif node_type == "business_context":
            state.business_context = data

        elif node_type in ("plan", "intent_classify"):
            state.metadata[node_type] = data

        elif node_type == "emit_telemetry":
            pass  # Side-effect only, no state merge

        elif node_type == "legacy_orchestrator_pipeline":
            if isinstance(data, dict):
                if "synthesis_result" in data:
                    synth = data["synthesis_result"]
                    state.synthesis_result = (
                        SynthesisResult(**synth) if isinstance(synth, dict) else synth
                    )
                if "retrieved_chunks" in data:
                    chunks = data["retrieved_chunks"]
                    state.retrieved_chunks = [
                        RetrievedChunk(**c) if isinstance(c, dict) else c
                        for c in chunks
                    ]

        else:
            # Unknown node type — store in metadata for inspection
            logger.debug(
                "No specific merge handler for node_type='%s', storing in metadata",
                node_type,
            )
            state.metadata[f"node_output_{output.node_id}"] = data

    # ═══════════════════════════════════════════════════════════════════════════
    # Budget Enforcement
    # ═══════════════════════════════════════════════════════════════════════════

    def _check_budget(
        self,
        state: StrategyRunState,
        budgets: Optional[StrategyBudgets],
    ) -> Optional[str]:
        """Check budget constraints before starting a level.

        Returns:
            halt_reason string if a hard limit is breached, None otherwise.
            Soft limit breaches are logged as warnings but do not halt.
        """
        if budgets is None:
            return None

        # ── Hard limits ──

        # Latency hard limit
        if budgets.latency_hard_limit_ms and state.elapsed_ms > budgets.latency_hard_limit_ms:
            return (
                f"latency_hard_limit_exceeded: "
                f"{state.elapsed_ms:.0f}ms > {budgets.latency_hard_limit_ms}ms"
            )

        # Token budget
        total_tokens = sum(
            out.tokens_used for out in state.node_outputs.values()
        )
        if budgets.max_context_tokens and total_tokens > budgets.max_context_tokens:
            return (
                f"token_budget_exceeded: "
                f"{total_tokens} > {budgets.max_context_tokens}"
            )

        # LLM call limit
        llm_calls = sum(
            1 for out in state.node_outputs.values() if out.tokens_used > 0
        )
        if budgets.max_llm_calls and llm_calls > budgets.max_llm_calls:
            return (
                f"llm_call_limit_exceeded: "
                f"{llm_calls} > {budgets.max_llm_calls}"
            )

        # ── Soft limits (warning only) ──

        if budgets.latency_target_ms and state.elapsed_ms > budgets.latency_target_ms:
            logger.warning(
                "Latency target exceeded: %.0fms > %dms (soft limit, continuing)",
                state.elapsed_ms,
                budgets.latency_target_ms,
            )

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Finalization
    # ═══════════════════════════════════════════════════════════════════════════

    def _finalize(
        self,
        state: StrategyRunState,
        halt_reason: Optional[str],
        spec: StrategySpec,
    ) -> StrategyRunResult:
        """Build the final StrategyRunResult from accumulated state.

        Args:
            state: The final run state after execution
            halt_reason: Why execution halted, or None for successful completion
            spec: The strategy spec (for IDs)

        Returns:
            Complete StrategyRunResult.
        """
        # Compute aggregate metrics
        total_tokens = sum(
            out.tokens_used for out in state.node_outputs.values()
        )
        total_llm_calls = sum(
            1 for out in state.node_outputs.values() if out.tokens_used > 0
        )
        nodes_executed = sum(
            1
            for out in state.node_outputs.values()
            if out.status == "success"
        )
        nodes_skipped = sum(
            1
            for out in state.node_outputs.values()
            if out.status in ("skipped", "empty")
        )

        # Determine success: no halt + synthesis produced
        success = halt_reason is None and state.synthesis_result is not None

        result = StrategyRunResult(
            success=success,
            state=state,
            strategy_id=spec.strategy_id,
            strategy_spec_hash=spec.spec_hash,
            halt_reason=halt_reason,
            total_duration_ms=state.elapsed_ms,
            total_tokens=total_tokens,
            total_llm_calls=total_llm_calls,
            nodes_executed=nodes_executed,
            nodes_skipped=nodes_skipped,
        )

        log_fn = logger.info if success else logger.warning
        log_fn(
            "Strategy run complete: success=%s, strategy_id=%s, "
            "duration=%.0fms, nodes_executed=%d, nodes_skipped=%d, "
            "halt_reason=%s",
            success,
            spec.strategy_id,
            state.elapsed_ms,
            nodes_executed,
            nodes_skipped,
            halt_reason,
        )

        return result
