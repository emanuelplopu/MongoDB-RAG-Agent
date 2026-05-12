"""Activity logger service for comprehensive agent observability.

Collects agent activity with role-based verbosity:
- Admin: Full verbose logging (complete requests, responses, tool calls, search results)
- User: Non-sensitive diagnostic data (timing, errors, document counts)

Data is persisted to MongoDB ``agent_activity_log`` collection with fire-and-forget writes.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Keys that contain sensitive content and should be stripped for non-admin users.
_SENSITIVE_KEYS = frozenset({
    "content",
    "messages",
    "prompt",
    "response",
    "arguments",
    "documents",
    "system_prompt",
    "full_response",
    "raw_output",
})

# Keys that are always safe to retain regardless of role.
_SAFE_KEYS = frozenset({
    "model",
    "provider",
    "duration_ms",
    "tokens_used",
    "count",
    "results_count",
    "category",
    "error",
    "phase",
    "query",
    "tool_name",
    "source",
    "status",
    "finish_reason",
    "temperature",
    "max_tokens",
    "success",
    "request_id",
    "session_id",
    "user_id",
    "timestamp",
    "elapsed_ms",
    "api_base",
    "scores",
})

COLLECTION_NAME = "agent_activity_log"


class ActivityLogger:
    """Async activity logger with role-based verbosity.

    Collects structured log entries during a request lifecycle and persists
    them to MongoDB in a single fire-and-forget write at flush time.

    Attributes:
        is_admin: Whether the current user has admin privileges.
        request_id: Correlation ID for the current request.
        session_id: Chat session ID.
        user_id: Authenticated user ID.
    """

    def __init__(
        self,
        db,
        is_admin: bool,
        request_id: str,
        session_id: str,
        user_id: str,
    ):
        """Initialize activity logger.

        Args:
            db: MongoDB database instance (pymongo async or motor).
            is_admin: Whether the user is an admin (enables verbose logging).
            request_id: Unique request correlation ID.
            session_id: Chat session ID.
            user_id: User's ID.
        """
        self._db = db
        self.is_admin: bool = is_admin
        self.request_id: str = request_id
        self.session_id: str = session_id
        self.user_id: str = user_id

        self._entries: List[Dict[str, Any]] = []
        self._start_time: float = time.perf_counter()
        self._start_datetime: datetime = datetime.utcnow()
        self._last_entry_time: float = self._start_time
        self._total_tokens: int = 0
        self._models_used: set = set()

    # ------------------------------------------------------------------
    # Public logging interface
    # ------------------------------------------------------------------

    def log(self, category: str, data: dict, sensitive: bool = False) -> None:
        """Add a log entry. Sensitive data only stored for admin.

        Categories: ``"llm_request"``, ``"llm_response"``, ``"search"``,
        ``"tool_call"``, ``"phase"``, ``"system_state"``, ``"error"``

        Args:
            category: Entry category.
            data: Entry data dict.
            sensitive: If True, only stored when ``is_admin=True``.
        """
        if sensitive and not self.is_admin:
            return

        now = time.perf_counter()
        elapsed_ms = round((now - self._last_entry_time) * 1000, 2)
        self._last_entry_time = now

        entry: Dict[str, Any] = {
            "category": category,
            "timestamp": datetime.utcnow().isoformat(),
            "elapsed_ms": elapsed_ms,
        }

        if self.is_admin:
            entry["data"] = data
        else:
            entry["data"] = self._strip_sensitive(data)

        self._entries.append(entry)

    def log_llm_request(
        self,
        model: str,
        provider: str,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 0,
        api_base: str = "",
        phase: str = "",
        **kwargs: Any,
    ) -> None:
        """Log an LLM request with full details for admin, minimal for user.

        Args:
            model: Model identifier (e.g. ``"gpt-4o"``).
            provider: Provider name (e.g. ``"openai"``, ``"azure"``).
            messages: Full message list sent to the LLM.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens requested.
            api_base: API base URL (no secrets).
            phase: Current agent phase name.
            **kwargs: Additional metadata fields.
        """
        self._models_used.add(model)

        data: Dict[str, Any] = {
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "phase": phase,
            "message_count": len(messages),
            **kwargs,
        }

        if self.is_admin:
            data["messages"] = messages
            if api_base:
                data["api_base"] = api_base

        self.log("llm_request", data)

    def log_llm_response(
        self,
        model: str,
        content: str,
        tokens_used: int,
        duration_ms: float,
        finish_reason: str = "",
        phase: str = "",
        **kwargs: Any,
    ) -> None:
        """Log an LLM response with full content for admin, metrics only for user.

        Args:
            model: Model that produced the response.
            content: Full response content.
            tokens_used: Total tokens consumed.
            duration_ms: Response latency in milliseconds.
            finish_reason: LLM finish reason (e.g. ``"stop"``).
            phase: Current agent phase name.
            **kwargs: Additional metadata fields.
        """
        self._total_tokens += tokens_used
        self._models_used.add(model)

        data: Dict[str, Any] = {
            "model": model,
            "tokens_used": tokens_used,
            "duration_ms": round(duration_ms, 2),
            "finish_reason": finish_reason,
            "phase": phase,
            **kwargs,
        }

        if self.is_admin:
            data["content"] = content
            data["content_length"] = len(content)
        else:
            data["content_length"] = len(content)

        self.log("llm_response", data)

    def log_search(
        self,
        query: str,
        source: str,
        results_count: int,
        documents: Optional[list] = None,
        scores: Optional[list] = None,
        duration_ms: float = 0,
        **kwargs: Any,
    ) -> None:
        """Log a search operation with full results for admin.

        Args:
            query: Search query string.
            source: Search source/index name.
            results_count: Number of results returned.
            documents: Full document results (admin only).
            scores: Relevance scores.
            duration_ms: Search latency in milliseconds.
            **kwargs: Additional metadata fields.
        """
        data: Dict[str, Any] = {
            "query": query,
            "source": source,
            "results_count": results_count,
            "duration_ms": round(duration_ms, 2),
            **kwargs,
        }

        if scores is not None:
            data["scores"] = scores

        if self.is_admin and documents is not None:
            data["documents"] = documents

        self.log("search", data)

    def log_tool_call(
        self,
        tool_name: str,
        arguments: dict,
        result: Any,
        duration_ms: float = 0,
        success: bool = True,
        **kwargs: Any,
    ) -> None:
        """Log a tool call with all arguments for admin.

        Args:
            tool_name: Name of the tool invoked.
            arguments: Tool call arguments.
            result: Tool call result.
            duration_ms: Execution time in milliseconds.
            success: Whether the tool call succeeded.
            **kwargs: Additional metadata fields.
        """
        data: Dict[str, Any] = {
            "tool_name": tool_name,
            "duration_ms": round(duration_ms, 2),
            "success": success,
            **kwargs,
        }

        if self.is_admin:
            data["arguments"] = arguments
            data["result"] = result
        else:
            data["argument_keys"] = list(arguments.keys()) if arguments else []

        self.log("tool_call", data)

    def log_phase(
        self,
        phase: str,
        status: str,
        duration_ms: float = 0,
        details: Optional[dict] = None,
    ) -> None:
        """Log a phase transition.

        Args:
            phase: Phase name (e.g. ``"retrieval"``, ``"generation"``).
            status: Phase status (e.g. ``"started"``, ``"completed"``, ``"failed"``).
            duration_ms: Phase duration in milliseconds.
            details: Additional phase details.
        """
        data: Dict[str, Any] = {
            "phase": phase,
            "status": status,
            "duration_ms": round(duration_ms, 2),
        }

        if details:
            if self.is_admin:
                data["details"] = details
            else:
                data["details"] = self._strip_sensitive(details)

        self.log("phase", data)

    def log_error(self, error: str, context: Optional[dict] = None) -> None:
        """Log an error (always collected regardless of role).

        Args:
            error: Error message string.
            context: Additional error context.
        """
        data: Dict[str, Any] = {"error": error}

        if context:
            if self.is_admin:
                data["context"] = context
            else:
                data["context"] = self._strip_sensitive(context)

        # Errors are never sensitive — always log them.
        self.log("error", data)

    def log_system_state(self, state: dict) -> None:
        """Log current system state snapshot (admin only).

        Args:
            state: System state dictionary (memory usage, queue depth, etc.).
        """
        self.log("system_state", state, sensitive=True)

    # ------------------------------------------------------------------
    # Diagnostic output
    # ------------------------------------------------------------------

    def get_user_diagnostic(self) -> dict:
        """Produce sanitized diagnostic payload for user support.

        Returns a dict safe to show to end users (no secrets, no full content).
        Contains timing, entry counts, error summaries, and request metadata.

        Returns:
            Sanitized diagnostic dictionary.
        """
        total_duration_ms = round(
            (time.perf_counter() - self._start_time) * 1000, 2
        )
        errors = [
            e["data"].get("error", "unknown")
            for e in self._entries
            if e["category"] == "error"
        ]

        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "total_entries": len(self._entries),
            "total_duration_ms": total_duration_ms,
            "total_tokens": self._total_tokens,
            "total_errors": len(errors),
            "errors": errors[:10],  # Cap to avoid huge payloads
            "models_used": sorted(self._models_used),
            "phases": [
                e["data"].get("phase", "")
                for e in self._entries
                if e["category"] == "phase"
            ],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _strip_sensitive(self, data: dict) -> dict:
        """Remove sensitive fields from data for non-admin logging.

        Strips keys containing full content (messages, prompts, responses,
        document text) while retaining safe operational metrics.

        Args:
            data: Original data dictionary.

        Returns:
            New dictionary with sensitive keys removed.
        """
        return {k: v for k, v in data.items() if k not in _SENSITIVE_KEYS}

    def _build_summary(self) -> dict:
        """Build a summary of all collected entries.

        Returns:
            Summary dictionary with aggregated metrics.
        """
        total_duration_ms = round(
            (time.perf_counter() - self._start_time) * 1000, 2
        )

        phases_completed = [
            e["data"].get("phase", "")
            for e in self._entries
            if e["category"] == "phase" and e["data"].get("status") == "completed"
        ]

        # Aggregate orchestrator phase durations for performance benchmarking
        orchestrator_phase_names = {"analyze", "plan", "evaluate", "synthesize"}
        orchestrator_ms = sum(
            e["data"].get("duration_ms", 0)
            for e in self._entries
            if e["category"] == "phase"
            and e["data"].get("status") == "completed"
            and e["data"].get("phase", "") in orchestrator_phase_names
        )

        avg_tokens_per_second = (
            round(self._total_tokens / (total_duration_ms / 1000), 1)
            if total_duration_ms > 0 and self._total_tokens > 0 else 0.0
        )

        cold_start_detected = any(
            e.get("data", {}).get("is_cold_start")
            for e in self._entries
        )

        return {
            "total_entries": len(self._entries),
            "total_llm_calls": sum(
                1 for e in self._entries if e["category"] in ("llm_request", "llm_response")
            ),
            "total_searches": sum(
                1 for e in self._entries if e["category"] == "search"
            ),
            "total_errors": sum(
                1 for e in self._entries if e["category"] == "error"
            ),
            "total_tokens": self._total_tokens,
            "total_duration_ms": total_duration_ms,
            "phases_completed": phases_completed,
            "models_used": sorted(self._models_used),
            "performance": {
                "total_duration_ms": total_duration_ms,
                "orchestrator_ms": round(orchestrator_ms, 2),
                "avg_tokens_per_second": avg_tokens_per_second,
                "overhead_ms": round(max(0.0, total_duration_ms - orchestrator_ms), 2),
                "cold_start_detected": cold_start_detected,
            },
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Persist collected entries to MongoDB (fire-and-forget).

        Does NOT block the request — uses ``asyncio.create_task``.
        Safe to call even if no entries were logged.
        """
        if not self._entries:
            return

        now = datetime.utcnow()
        total_duration_ms = round(
            (time.perf_counter() - self._start_time) * 1000, 2
        )

        doc: Dict[str, Any] = {
            "_id": self.request_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "is_admin": self.is_admin,
            "started_at": self._start_datetime,
            "completed_at": now,
            "duration_ms": total_duration_ms,
            "summary": self._build_summary(),
            "entries": self._entries,
        }

        asyncio.create_task(self._persist(doc))

    async def _persist(self, doc: dict) -> None:
        """Internal: write document to MongoDB.

        Catches all exceptions to guarantee request stability.

        Args:
            doc: Document to insert into the activity log collection.
        """
        try:
            collection = self._db[COLLECTION_NAME]
            await collection.insert_one(doc)
        except Exception as exc:
            logger.warning(
                "Failed to persist activity log for request %s: %s",
                doc.get("request_id", "unknown"),
                exc,
            )


class NullActivityLogger:
    """No-op logger for when logging is disabled or unavailable.

    Implements the same interface as :class:`ActivityLogger` but does nothing.
    Used as a safe fallback to avoid ``None`` checks everywhere.
    """

    def log(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_llm_request(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_llm_response(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_search(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_tool_call(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_phase(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_error(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log_system_state(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_user_diagnostic(self) -> dict:
        """Return empty diagnostic payload."""
        return {}

    async def flush(self) -> None:
        pass
