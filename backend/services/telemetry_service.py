"""
Development telemetry service.

Captures interaction traces with PII pseudonymization for data-driven development.
Records are written as daily JSONL files with automatic rotation.

The service is entirely non-blocking: failures are logged but never crash the request.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import asyncio

from backend.models.telemetry import (
    TelemetryRecord, ToolCall, SearchHit,
    LLMCall, SearchOperation, ToolExecution, PhaseMetrics,
)
from backend.services.pii_pseudonymizer import TelemetryPseudonymizer

logger = logging.getLogger(__name__)


class TelemetryService:
    """Collects and stores pseudonymized telemetry records.

    Thread-safe async writes to daily JSONL files.
    All operations are non-blocking and failure-tolerant.
    """

    def __init__(
        self,
        storage_path: str = "data/telemetry",
        enabled: bool = True,
        mode: str = "dev",
        pii_mode: str = "both",
        retention_days: int = 90,
        pii_markers: bool = True,
    ) -> None:
        """Initialize the telemetry service.

        Args:
            storage_path: Directory for JSONL telemetry files.
            enabled: Whether telemetry collection is active.
            mode: Operating mode — 'dev', 'beta', 'production', or 'disabled'.
            pii_mode: PII handling — 'both', 'protected_only', 'raw_only', or 'disabled'.
            retention_days: How many days to keep telemetry files.
            pii_markers: Wrap PII replacements with ``[PII:TYPE]...[/PII]`` markers.
        """
        self._storage_path = Path(storage_path)
        self._enabled = enabled
        self._mode = mode  # dev | beta | production | disabled
        self._pii_mode = pii_mode  # both | protected_only | raw_only | disabled
        self._retention_days = retention_days
        self._pii_markers = pii_markers
        self._pseudonymizer = (
            TelemetryPseudonymizer() if enabled and pii_mode != "raw_only" else None
        )
        self._write_lock = asyncio.Lock()
        self._raw_write_lock = asyncio.Lock()

        if enabled:
            self._storage_path.mkdir(parents=True, exist_ok=True)
            if pii_mode in ("both", "raw_only"):
                (self._storage_path / "raw").mkdir(parents=True, exist_ok=True)
            logger.info(
                f"Telemetry service enabled: mode={mode}, pii_mode={pii_mode}"
            )
        else:
            logger.info("Telemetry service disabled")

    @property
    def enabled(self) -> bool:
        """Whether telemetry collection is active."""
        return self._enabled and self._mode != "disabled"

    async def record_interaction(
        self,
        session_id: str,
        user_id: str,
        tenant: str,
        prompt: str,
        response: str,
        agent_thinking: str = "",
        agent_strategy: str = "",
        tools_called: list[ToolCall] | None = None,
        search_queries: list[str] | None = None,
        search_results: list[SearchHit] | None = None,
        model_used: str = "",
        latency_ms: int = 0,
        prompt_tokens: int = 0,
        response_tokens: int = 0,
        hybrid_search_scores: dict | None = None,
        sources_cited: list[str] | None = None,
        confidence_score: float | None = None,
        language: str = "de",
        app_version: str = "",
        # === FULL CAPTURE PARAMS ===
        llm_calls: list | None = None,
        search_operations: list | None = None,
        tool_executions: list | None = None,
        phase_metrics: list | None = None,
        agent_mode: str = "",
        orchestrator_model: str = "",
        worker_model: str = "",
        orchestrator_provider: str = "",
        worker_provider: str = "",
        orchestrator_duration_ms: int = 0,
        worker_duration_ms: int = 0,
        total_duration_ms: int = 0,
        tokens_per_model: dict | None = None,
        early_exit_triggered: bool = False,
        early_exit_confidence: float | None = None,
        total_sources_found: int = 0,
        deduplicated_sources: int = 0,
        strategy_os_context: dict | None = None,
    ) -> Optional[str]:
        """Record a complete interaction with automatic PII pseudonymization.

        Args:
            session_id: Chat session identifier.
            user_id: Raw user ID (will be pseudonymized).
            tenant: Tenant identifier (recallhub/quellex).
            prompt: User's prompt text.
            response: Agent's response text.
            agent_thinking: Agent reasoning trace.
            agent_strategy: Strategy name used.
            tools_called: List of tool calls made.
            search_queries: Search queries executed.
            search_results: Search result summaries.
            model_used: LLM model identifier.
            latency_ms: Total response latency.
            prompt_tokens: Token count for prompt.
            response_tokens: Token count for response.
            hybrid_search_scores: Search scoring details.
            sources_cited: Document sources referenced.
            confidence_score: Agent confidence score.
            language: Interaction language code.
            app_version: Application version string.

        Returns:
            Record ID if successful, None on failure.
        """
        if not self.enabled:
            return None

        try:
            # --- Write RAW record (no pseudonymization) ---
            if self._pii_mode in ("both", "raw_only"):
                raw_record = TelemetryRecord(
                    session_id=session_id,
                    user_id=user_id,  # Real user ID in raw
                    tenant=tenant,
                    app_version=app_version,
                    prompt_original=prompt,
                    prompt_pseudonymized=prompt,  # Not actually pseudonymized in raw
                    prompt_tokens=prompt_tokens,
                    prompt_language=language,
                    agent_strategy=agent_strategy,
                    agent_thinking=agent_thinking,
                    tools_called=tools_called or [],
                    search_queries=search_queries or [],
                    search_results_summary=search_results or [],
                    response_pseudonymized=response,
                    response_tokens=response_tokens,
                    response_latency_ms=latency_ms,
                    model_used=model_used,
                    hybrid_search_scores=hybrid_search_scores or {},
                    sources_cited=sources_cited or [],
                    confidence_score=confidence_score,
                    pii_entities_detected=0,
                    entity_types_found=[],
                    # Full capture fields
                    llm_calls=llm_calls or [],
                    search_operations=search_operations or [],
                    tool_executions=tool_executions or [],
                    phase_metrics=phase_metrics or [],
                    agent_mode=agent_mode,
                    orchestrator_model=orchestrator_model,
                    worker_model=worker_model,
                    orchestrator_provider=orchestrator_provider,
                    worker_provider=worker_provider,
                    orchestrator_duration_ms=orchestrator_duration_ms,
                    worker_duration_ms=worker_duration_ms,
                    total_duration_ms=total_duration_ms,
                    tokens_per_model=tokens_per_model or {},
                    early_exit_triggered=early_exit_triggered,
                    early_exit_confidence=early_exit_confidence,
                    total_sources_found=total_sources_found,
                    deduplicated_sources=deduplicated_sources,
                    capability_id=strategy_os_context.get("capability_id") if strategy_os_context else None,
                )
                await self._write_raw_record(raw_record)

            # --- Write PROTECTED record (with pseudonymization) ---
            if self._pii_mode in ("both", "protected_only"):
                # Pseudonymize all text fields
                prompt_pseudo, prompt_stats = self._pseudonymizer.pseudonymize(
                    prompt, session_id, language, include_markers=self._pii_markers
                )
                response_pseudo, _ = self._pseudonymizer.pseudonymize(
                    response, session_id, language, include_markers=self._pii_markers
                )
                thinking_pseudo, _ = self._pseudonymizer.pseudonymize(
                    agent_thinking, session_id, language, include_markers=self._pii_markers
                )

                # Pseudonymize search queries
                queries_pseudo: list[str] = []
                if search_queries:
                    for q in search_queries:
                        q_pseudo, _ = self._pseudonymizer.pseudonymize(
                            q, session_id, language, include_markers=self._pii_markers
                        )
                        queries_pseudo.append(q_pseudo)

                # Pseudonymize search result previews
                results_pseudo: list[SearchHit] = []
                if search_results:
                    for hit in search_results:
                        preview_pseudo, _ = self._pseudonymizer.pseudonymize(
                            hit.chunk_preview, session_id, language,
                            include_markers=self._pii_markers,
                        )
                        results_pseudo.append(
                            SearchHit(
                                document_id=hit.document_id,
                                score=hit.score,
                                chunk_preview=preview_pseudo,
                            )
                        )

                # Pseudonymize detailed records
                if llm_calls:
                    for call in llm_calls:
                        if hasattr(call, 'prompt_text') and call.prompt_text:
                            call.prompt_text, _ = self._pseudonymizer.pseudonymize(
                                call.prompt_text, session_id, language,
                                include_markers=self._pii_markers,
                            )
                        if hasattr(call, 'response_text') and call.response_text:
                            call.response_text, _ = self._pseudonymizer.pseudonymize(
                                call.response_text, session_id, language,
                                include_markers=self._pii_markers,
                            )

                if search_operations:
                    for op in search_operations:
                        if hasattr(op, 'query') and op.query:
                            op.query, _ = self._pseudonymizer.pseudonymize(
                                op.query, session_id, language,
                                include_markers=self._pii_markers,
                            )
                        if hasattr(op, 'chunks_returned'):
                            op.chunks_returned = [
                                self._pseudonymizer.pseudonymize(
                                    c, session_id, language,
                                    include_markers=self._pii_markers,
                                )[0]
                                for c in (op.chunks_returned or [])
                            ]

                if tool_executions:
                    for ex in tool_executions:
                        if hasattr(ex, 'input_query') and ex.input_query:
                            ex.input_query, _ = self._pseudonymizer.pseudonymize(
                                ex.input_query, session_id, language,
                                include_markers=self._pii_markers,
                            )
                        if hasattr(ex, 'output_text') and ex.output_text:
                            ex.output_text, _ = self._pseudonymizer.pseudonymize(
                                ex.output_text, session_id, language,
                                include_markers=self._pii_markers,
                            )

                # Build protected record
                record = TelemetryRecord(
                    session_id=session_id,
                    user_id=f"user_{hash(user_id) % 100000:05d}",  # Pseudonymized
                    tenant=tenant,
                    app_version=app_version,
                    prompt_original=prompt_pseudo if self._mode == "dev" else None,
                    prompt_pseudonymized=prompt_pseudo,
                    prompt_tokens=prompt_tokens,
                    prompt_language=language,
                    agent_strategy=agent_strategy,
                    agent_thinking=thinking_pseudo,
                    tools_called=tools_called or [],
                    search_queries=queries_pseudo,
                    search_results_summary=results_pseudo,
                    response_pseudonymized=response_pseudo,
                    response_tokens=response_tokens,
                    response_latency_ms=latency_ms,
                    model_used=model_used,
                    hybrid_search_scores=hybrid_search_scores or {},
                    sources_cited=sources_cited or [],
                    confidence_score=confidence_score,
                    pii_entities_detected=prompt_stats.get("entities_detected", 0),
                    entity_types_found=prompt_stats.get("types_found", []),
                    # Full capture fields
                    llm_calls=llm_calls or [],
                    search_operations=search_operations or [],
                    tool_executions=tool_executions or [],
                    phase_metrics=phase_metrics or [],
                    agent_mode=agent_mode,
                    orchestrator_model=orchestrator_model,
                    worker_model=worker_model,
                    orchestrator_provider=orchestrator_provider,
                    worker_provider=worker_provider,
                    orchestrator_duration_ms=orchestrator_duration_ms,
                    worker_duration_ms=worker_duration_ms,
                    total_duration_ms=total_duration_ms,
                    tokens_per_model=tokens_per_model or {},
                    early_exit_triggered=early_exit_triggered,
                    early_exit_confidence=early_exit_confidence,
                    total_sources_found=total_sources_found,
                    deduplicated_sources=deduplicated_sources,
                    capability_id=strategy_os_context.get("capability_id") if strategy_os_context else None,
                )

                # Write to daily JSONL file
                await self._write_record(record)
                return record.record_id

            return None

        except Exception as e:
            logger.error(f"Telemetry recording failed: {e}", exc_info=True)
            return None

    async def _write_record(self, record: TelemetryRecord) -> None:
        """Append record to daily JSONL file (PII-protected).

        Args:
            record: The telemetry record to persist.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        filepath = self._storage_path / f"{today}.jsonl"

        async with self._write_lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")

    async def _write_raw_record(self, record: TelemetryRecord) -> None:
        """Append record to daily RAW JSONL file (no PII protection).

        Args:
            record: The telemetry record to persist without pseudonymization.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        filepath = self._storage_path / "raw" / f"{today}.jsonl"

        async with self._raw_write_lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")

    async def rotate_old_files(self) -> None:
        """Remove telemetry files older than retention period."""
        if not self._storage_path.exists():
            return

        cutoff = datetime.utcnow() - timedelta(days=self._retention_days)
        removed = 0

        # Rotate protected files
        for filepath in self._storage_path.glob("*.jsonl"):
            try:
                file_date = datetime.strptime(filepath.stem, "%Y-%m-%d")
                if file_date < cutoff:
                    filepath.unlink()
                    removed += 1
            except ValueError:
                continue

        # Rotate raw files
        raw_dir = self._storage_path / "raw"
        if raw_dir.exists():
            for filepath in raw_dir.glob("*.jsonl"):
                try:
                    file_date = datetime.strptime(filepath.stem, "%Y-%m-%d")
                    if file_date < cutoff:
                        filepath.unlink()
                        removed += 1
                except ValueError:
                    continue

        if removed:
            logger.info(
                f"Telemetry rotation: removed {removed} files older than "
                f"{self._retention_days} days"
            )

    def cleanup_session(self, session_id: str) -> None:
        """Clean up session-specific entity registry.

        Args:
            session_id: The session to clean up.
        """
        if self._pseudonymizer:
            self._pseudonymizer.cleanup_session(session_id)
