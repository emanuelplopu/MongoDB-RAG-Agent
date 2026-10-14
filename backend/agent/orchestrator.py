"""Orchestrator - High-level thinking model for planning and synthesis.

The Orchestrator is responsible for:
1. Analyzing user intent
2. Creating a search/action plan
3. Evaluating worker results
4. Synthesizing final responses

It uses a "thinking" model (like GPT-5.1 or o1) for complex reasoning.
Prompts are loaded from the database for easy customization,
or from the active strategy if one is provided.
"""

import json
import logging
import time
import zlib
from typing import List, Optional, Dict, Any, Set, TYPE_CHECKING

from backend.agent.schemas import (
    DataSourceType, TaskDefinition, TaskType, AgentPlan, EvaluationDecision,
    OrchestratorStep, OrchestratorPhase, WorkerResult, DocumentReference
)
from backend.agent.tool_gate import ToolGate
from backend.core.config import settings
from backend.services.activity_logger import NullActivityLogger
from backend.models.telemetry import LLMCall
try:
    from backend.routers.prompts import get_agent_prompt_sync, DEFAULT_AGENT_PROMPTS
except ImportError:
    DEFAULT_AGENT_PROMPTS = {}

    def get_agent_prompt_sync(prompt_key: str) -> str:
        """Fallback used in tests when prompt router dependencies are unavailable."""
        return ""

if TYPE_CHECKING:
    from backend.agent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Phase-specific max_tokens limits to prevent runaway generation
_PHASE_MAX_TOKENS: Dict[str, int] = {
    "analyze": 2000,
    "plan": 2000,
    "evaluate": 1500,
    "synthesize": 4000,
}

# Module-level cold-start tracker: tracks which models have completed at least one call
_model_first_call_done: set = set()

# Search task types that only make sense when a source of the given type exists.
# search_profile and search_all are always possible and are not listed.
_SOURCE_TYPES_FOR_TASK: Dict[TaskType, Set[str]] = {
    TaskType.SEARCH_CLOUD: {DataSourceType.CLOUD_SHARED.value, DataSourceType.CLOUD_PRIVATE.value},
    TaskType.SEARCH_PERSONAL: {DataSourceType.PERSONAL.value},
}


_LOOP_MIN_LINE_CHARS = 40
_LOOP_MIN_LINE_REPEATS = 5
_LOOP_MAX_PERIOD = 200
_LOOP_MIN_SPAN = 120
_TRUNCATION_MARKER = "\n\n... [truncated: repetitive output detected]"
_JSON_RETRY_INSTRUCTION = (
    "\n\nIMPORTANT: your previous reply could not be parsed. Reply with exactly one valid JSON "
    "object and nothing else: no prose, no code fences, no comments."
)


def _repeated_line(text: str) -> Optional[str]:
    """The most frequent long line if it repeats 5+ times and makes up two thirds of all lines.

    Answers that put the same citation under every bullet reach one half, not two thirds.
    """
    counts: Dict[str, int] = {}
    non_empty = 0
    for line in text.splitlines():
        s = line.strip()
        non_empty += bool(s)
        if len(s) >= _LOOP_MIN_LINE_CHARS:
            counts[s] = counts.get(s, 0) + 1
    if not counts:
        return None
    line, n = max(counts.items(), key=lambda kv: kv[1])
    return line if n >= _LOOP_MIN_LINE_REPEATS and n * 3 >= non_empty * 2 else None


def _tail_period(text: str) -> int:
    """Length of a block that repeats back-to-back at the end of the text (0 if none)."""
    s = text.rstrip()
    for period in range(1, _LOOP_MAX_PERIOD + 1):
        reps = max(6, -(-_LOOP_MIN_SPAN // period))
        span = period * reps
        if span > len(s):
            break
        unit = s[-period:]
        if unit.strip() and s[-span:] == unit * reps:
            return period
    return 0


def _detect_repetition(text: str, threshold: float = 0.10, check_lines: bool = True) -> bool:
    """Detect output that degenerated into a loop.

    Any of three signals: one long line (40+ chars) repeated 5+ times and making up
    two thirds of all lines; a block repeated back-to-back at the end (the usual way a
    looping generation ends); or, for long text, a zlib compression ratio below
    ``threshold``. Indented JSON and legal answers with repeated citations stay clear
    of all three (the previous check, any 10-char substring 6+ times, fired on JSON
    indentation and cut valid output to 200 chars).

    Args:
        text: Model output.
        threshold: Compression ratio below which long text counts as a loop.
        check_lines: Use the repeated-line signal (off for JSON, where list items repeat keys).

    Returns:
        True if the text appears to be repetitive garbage.
    """
    if not text or len(text) < 100:
        return False
    if (check_lines and _repeated_line(text)) or _tail_period(text):
        return True
    if len(text) >= 1500:
        return len(zlib.compress(text.encode("utf-8"))) / len(text.encode("utf-8")) < threshold
    return False


def _trim_repetition(text: str) -> str:
    """Cut looping output where the loop starts and mark it; unchanged if the loop cannot be located."""
    s = text.rstrip()
    period = _tail_period(s)
    if period:
        i = len(s) - period
        while i - period >= 0 and s[i - period:i] == s[i:i + period]:
            i -= period
        return s[:i + period] + _TRUNCATION_MARKER
    line = _repeated_line(s)
    if line:
        pos, seen = 0, 0
        while True:
            pos = s.find(line, pos)
            if pos == -1:
                break
            seen += 1
            if seen == 3:
                return s[:pos].rstrip() + _TRUNCATION_MARKER
            pos += len(line)
    return text


def _extract_json(content: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse the JSON object in a model reply: bare, in a code fence, or embedded in prose."""
    text = (content or "").strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        fenced = text.split("```json", 1)[1] if "```json" in text else text.split("```", 1)[1]
        candidates.insert(0, fenced.split("```", 1)[0].strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start, attempts = candidate.find("{"), 0
        while start != -1 and attempts < 20:
            try:
                obj, _ = decoder.raw_decode(candidate, start)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            start, attempts = candidate.find("{", start + 1), attempts + 1
    return None


def _clean_query(query: Any) -> str:
    """Normalise a planner query and strip JSON syntax that leaked into it.

    Returns "" when nothing usable is left (empty, or shorter than 3 characters).
    Quoted phrases stay; unbalanced quotes are removed.
    """
    s = " ".join(str(query or "").split()).strip(" ,")
    while s.startswith(('",', "',")):
        s = s[2:].strip(" ,")
    while s.endswith((',"', ",'")):
        s = s[:-2].strip(" ,")
    # A list separator inside: two quoted phrases merged into one query.
    s = s.replace('", "', '" "')
    if s.count('"') % 2 == 1:
        s = s.strip('"').strip()
        if s.count('"') % 2 == 1:
            s = " ".join(s.replace('"', " ").split())
    return s if len(s) >= 3 else ""


# Default prompts (used when database is not available)
# These are loaded from prompts.py and can be overridden in the database
def _get_default_prompt(key: str) -> str:
    """Get default prompt, falling back to hardcoded if needed."""
    return get_agent_prompt_sync(key)


class Orchestrator:
    """High-level thinking model that plans and coordinates searches."""
    
    def __init__(
        self,
        model: str = None,
        provider: str = None,
        strategy: "BaseStrategy" = None,
        activity_logger=None,
        req_id: str = "no-req"
    ):
        """Initialize orchestrator.
        
        Args:
            model: LLM model to use for orchestration
            provider: LLM provider (openai, google, anthropic)
            strategy: Strategy instance to use for prompts and processing
            activity_logger: ActivityLogger for observability (defaults to NullActivityLogger)
            req_id: Request correlation ID for structured logging
        """
        self.model = model or settings.orchestrator_model
        self.provider = provider or settings.orchestrator_provider
        self.strategy = strategy
        self.steps: List[OrchestratorStep] = []
        self._llm_calls: List[LLMCall] = []
        self._client = None
        self.activity_logger = activity_logger or NullActivityLogger()
        self.req_id = req_id
        self.degradations: List[str] = []
        self._user_message: str = ""
        self._unavailable_task_types: Set[TaskType] = set()

    def _get_model_string(self) -> str:
        """Get the model string in LiteLLM format with provider prefix."""
        provider = self.provider.lower()
        model = self.model
        
        if provider == "openai":
            return model  # No prefix needed
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
    
    def reset(self):
        """Reset steps for a new session."""
        self.steps = []
        self._llm_calls = []
        self.degradations = []
        self._user_message = ""
        self._unavailable_task_types = set()

    def _degrade(self, name: str) -> None:
        """Record a fallback path taken in this request; it ends up in the trace's ``degradations``."""
        self.degradations.append(name)
        logger.info(f"[req={self.req_id}] degradation: {name}")

    @staticmethod
    def unavailable_task_types(available_sources: List[Dict[str, Any]]) -> Set[TaskType]:
        """Search task types that none of the available sources can serve.

        ``search_cloud`` needs a cloud source and ``search_personal`` a personal
        one; a user without a synced cloud drive or ingested mailbox has neither,
        and planning them only produces empty searches. Profile and all-source
        searches are always possible.

        Args:
            available_sources: Source dicts with a ``type`` key (``DataSourceType`` value).

        Returns:
            The task types the planner must not use for this request.
        """
        present = {str(s.get("type", "")) for s in available_sources}
        return {
            task_type for task_type, source_types in _SOURCE_TYPES_FOR_TASK.items()
            if not (present & source_types)
        }

    def _drop_unavailable_tasks(self, tasks: List[TaskDefinition], phase: str) -> List[TaskDefinition]:
        """Remove tasks whose type has no source behind it, recording a degradation once."""
        if not self._unavailable_task_types:
            return tasks
        kept = [t for t in tasks if t.type not in self._unavailable_task_types]
        if len(kept) < len(tasks):
            dropped = [f"{t.id}:{t.type}" for t in tasks if t.type in self._unavailable_task_types]
            logger.info(f"[req={self.req_id}] {phase}: dropped tasks without a source: {dropped}")
            self._degrade(f"{phase}_unavailable_task_type")
        return kept
    
    def _get_prompt(self, phase: str) -> str:
        """Get prompt for a phase, using strategy if available.
        
        Args:
            phase: One of 'analyze', 'plan', 'evaluate', 'synthesize'
        
        Returns:
            Prompt template string
        """
        if self.strategy:
            prompt_methods = {
                'analyze': self.strategy.get_analyze_prompt,
                'plan': self.strategy.get_plan_prompt,
                'evaluate': self.strategy.get_evaluate_prompt,
                'synthesize': self.strategy.get_synthesize_prompt,
            }
            method = prompt_methods.get(phase)
            if method:
                return method()
        
        # Fallback to default prompts
        prompt_keys = {
            'analyze': 'agent_analyze',
            'plan': 'agent_plan',
            'evaluate': 'agent_evaluate',
            'synthesize': 'agent_synthesize',
        }
        return _get_default_prompt(prompt_keys.get(phase, f'agent_{phase}'))
    
    async def _call_llm(
        self,
        prompt: str,
        phase: OrchestratorPhase,
        expect_json: bool = True
    ) -> Dict[str, Any]:
        """Call the LLM, parse the output and record one step per attempt.

        A JSON phase whose output cannot be parsed is retried once with a stricter
        instruction (``settings.agent_json_retry``). Fallbacks are recorded in
        ``self.degradations``.

        Args:
            prompt: The prompt to send
            phase: Which phase this is
            expect_json: Whether to parse response as JSON

        Returns:
            Parsed response dict, ``{"response": text}`` if not JSON, or
            ``{"response": text, "parse_error": True}`` if JSON could not be parsed
        """
        phase_key = phase.value if hasattr(phase, 'value') else str(phase)
        content = await self._complete(prompt, phase, expect_json, attempt=1)
        if not expect_json:
            return {"response": content}

        result = _extract_json(content)
        if result is None and settings.agent_json_retry:
            self.steps[-1].parse_error = True
            self.steps[-1].raw_output = content[:4000]
            logger.warning(f"[req={self.req_id}] {phase_key}: output is not valid JSON, retrying once: {content[:200]!r}")
            content = await self._complete(prompt + _JSON_RETRY_INSTRUCTION, phase, expect_json, attempt=2)
            result = _extract_json(content)
            if result is not None:
                self._degrade(f"{phase_key}_json_retry")
        if result is None:
            step = self.steps[-1]
            step.parse_error = True
            step.raw_output = content[:4000]
            self._degrade(f"{phase_key}_parse_failed")
            logger.warning(f"[req={self.req_id}] Failed to parse JSON from orchestrator: {content[:200]}")
            return {"response": content, "parse_error": True}

        step = self.steps[-1]
        step.reasoning = str(result.get("reasoning") or content[:500])[:500]
        step.output_summary = json.dumps(result, ensure_ascii=False)[:500]
        return result

    async def _complete(self, prompt: str, phase: OrchestratorPhase, expect_json: bool, attempt: int) -> str:
        """One LLM call: provider options, loop guard, step record and telemetry.

        Returns:
            The (possibly trimmed) text output.
        """
        from litellm import acompletion
        from backend.agent.ollama_options import apply_ollama_options, think_budget

        start_time = time.time()
        phase_key = phase.value if hasattr(phase, 'value') else str(phase)
        max_tokens_for_phase = _PHASE_MAX_TOKENS.get(phase_key, 2000)
        model_string = self._get_model_string()
        request_options: Dict[str, Any] = {}

        try:
            api_key = settings.get_api_key_for_provider(self.provider)
            llm_params = {
                "model": model_string,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2 if phase != OrchestratorPhase.SYNTHESIZE else 0.7,
                "api_key": api_key,
            }

            # Set api_base for Ollama models so LiteLLM routes to the correct host
            is_ollama = self.provider.lower() == "ollama" or "ollama/" in model_string
            if is_ollama:
                llm_params["api_base"] = settings.ollama_base_url
                # Large local models (cold start + long generation) need a
                # generous per-call budget. Cloud providers use their own
                # server-side limits.
                llm_params["timeout"] = settings.agent_llm_request_timeout
                if not expect_json:
                    llm_params["stop"] = ["\n\n\n\n", "---END---"]
                request_options = await apply_ollama_options(llm_params, phase_key, json_mode=expect_json)
                max_tokens_for_phase += think_budget(request_options)

            # Check if this is a newer OpenAI model that requires max_completion_tokens
            model_lower = model_string.lower()
            needs_max_completion_tokens = any(prefix in model_lower for prefix in [
                "gpt-5", "gpt-4o", "gpt-4.1", "o1", "o3", "o4"
            ])
            if needs_max_completion_tokens:
                llm_params["max_completion_tokens"] = max_tokens_for_phase
            else:
                llm_params["max_tokens"] = max_tokens_for_phase
            request_options.update(temperature=llm_params["temperature"], max_tokens=max_tokens_for_phase)

            logger.info(f"[req={self.req_id}] Orchestrator LLM call: model={model_string}, provider={self.provider}, api_base={llm_params.get('api_base', 'default')}, phase={phase_key}, attempt={attempt}, options={request_options}")

            # Log the LLM request for observability
            self.activity_logger.log_llm_request(
                model=model_string, provider=self.provider,
                messages=llm_params["messages"],
                temperature=llm_params.get("temperature", 0),
                max_tokens=llm_params.get("max_tokens", llm_params.get("max_completion_tokens", 0)),
                api_base=llm_params.get("api_base", ""),
                phase=phase.value
            )

            response = await acompletion(**llm_params)

            raw_content = response.choices[0].message.content or ""
            usage = response.usage
            tokens_used = usage.total_tokens if usage else 0
            prompt_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
            completion_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0
            duration_ms = (time.time() - start_time) * 1000
            finish_reason = response.choices[0].finish_reason or ""
            # LiteLLM's Ollama adapter always reports "stop"; a completion that used its
            # whole budget was cut off.
            hit_limit = finish_reason == "length" or (completion_tokens >= max_tokens_for_phase > 0)
            # ~3.5 characters per token; the rest of the completion was hidden reasoning.
            hidden_tokens = max(0, completion_tokens - int(len(raw_content) / 3.5))

            content = raw_content
            repetition = _detect_repetition(raw_content, check_lines=not expect_json)
            truncated = False
            if repetition:
                logger.warning(f"[req={self.req_id}] Repetitive output detected in {phase.value} phase" + ("" if expect_json else ", truncating"))
                if not expect_json:
                    content = _trim_repetition(raw_content)
                    truncated = content != raw_content
                    if truncated:
                        self._degrade(f"{phase_key}_repetition_trimmed")
            if hit_limit:
                self._degrade(f"{phase_key}_token_limit")
                logger.warning(f"[req={self.req_id}] {phase_key}: completion used its whole budget ({completion_tokens}/{max_tokens_for_phase} tokens, ~{hidden_tokens} hidden)")

            # Log the LLM response for observability
            self.activity_logger.log_llm_response(
                model=model_string, content=content, tokens_used=tokens_used,
                duration_ms=duration_ms,
                finish_reason=finish_reason,
                phase=phase.value
            )

            # Calculate per-phase token throughput for hardware benchmarking
            tokens_per_second = (
                round(tokens_used / (duration_ms / 1000), 1)
                if duration_ms > 0 and tokens_used > 0 else 0.0
            )

            # Cold-start detection: first call per model is flagged
            is_cold_start = self.model not in _model_first_call_done
            _model_first_call_done.add(self.model)

            step = OrchestratorStep(
                phase=phase,
                model=self.model,
                input_summary=prompt[:200] + "..." if len(prompt) > 200 else prompt,
                reasoning=content[:500],
                output_summary=json.dumps({"response": content}, ensure_ascii=False)[:500],
                tokens_used=tokens_used,
                duration_ms=duration_ms,
                tokens_per_second=tokens_per_second,
                is_cold_start=is_cold_start,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                hidden_tokens_est=hidden_tokens,
                max_tokens=max_tokens_for_phase,
                hit_token_limit=hit_limit,
                content_chars=len(raw_content),
                attempt=attempt,
                repetition_detected=repetition,
                output_truncated=truncated,
                request_options=request_options,
                raw_output=raw_content[:4000] if truncated else None,
            )
            self.steps.append(step)

            llm_record = LLMCall(
                phase=phase_key,
                model=self.model,
                provider=self.provider,
                temperature=llm_params.get("temperature", 0.7),
                max_tokens=max_tokens_for_phase,
                prompt_text=prompt,
                response_text=content,
                prompt_tokens=prompt_tokens,
                response_tokens=completion_tokens,
                total_tokens=tokens_used,
                latency_ms=int(duration_ms),
                finish_reason="length" if hit_limit else finish_reason,
                success=True,
                is_cold_start=is_cold_start,
                repetition_detected=repetition,
            )
            self._llm_calls.append(llm_record)

            return content

        except Exception as e:
            logger.error(f"[req={self.req_id}] Orchestrator LLM call failed: {e}")
            self.activity_logger.log_error(str(e), context={"phase": phase.value, "model": model_string})
            duration_ms = (time.time() - start_time) * 1000

            # Record failed step
            step = OrchestratorStep(
                phase=phase,
                model=self.model,
                input_summary=prompt[:200] + "...",
                reasoning=f"Error: {str(e)}",
                output_summary="Failed",
                tokens_used=0,
                duration_ms=duration_ms,
                attempt=attempt,
                request_options=request_options,
                error=f"{type(e).__name__}: {e}"[:500],
            )
            self.steps.append(step)

            raise

    async def analyze(
        self,
        user_message: str,
        conversation_history: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Phase 1: Analyze user intent with enhanced context extraction.
        
        Args:
            user_message: The user's message
            conversation_history: Previous messages in conversation
        
        Returns:
            Analysis dict with intent, entities, sources needed
        """
        self._user_message = user_message
        history_str = self._extract_relevant_context(user_message, conversation_history)
        
        prompt = self._get_prompt("analyze").format(
            user_message=user_message,
            conversation_history=history_str
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.ANALYZE)
        
        # Use strategy's process_analysis if available
        if result.get("parse_error"):
            self._degrade("analyze_defaults_used")
        if self.strategy:
            result = self.strategy.process_analysis(result) or result
        else:
            # Set defaults for missing fields with new schema compatibility
            result.setdefault("intent_summary", user_message[:100])
            result.setdefault("query_type", "FACTUAL")
            result.setdefault("named_entities", {"people": [], "organizations": [], "documents": [], "dates": [], "technical_terms": []})
            result.setdefault("search_queries", {"primary": user_message, "alternatives": []})
            result.setdefault("sources_needed", ["profile", "personal"])
            result.setdefault("source_priority", "profile")
            result.setdefault("must_find", [])
            result.setdefault("nice_to_have", [])
            result.setdefault("complexity", 2)
            result.setdefault("requires_multi_hop", False)
            
            # Legacy field compatibility
            result.setdefault("key_entities", self._flatten_entities(result.get("named_entities", {})))
            result.setdefault("is_complex", result.get("complexity", 2) >= 3)
            result.setdefault("requires_multiple_searches", result.get("complexity", 2) >= 2)

        # Strategies default these to "" - the planner would then search with an empty query.
        if not str(result.get("intent_summary") or "").strip():
            result["intent_summary"] = user_message[:200]
        if not isinstance(result.get("search_queries"), dict):
            result["search_queries"] = {"primary": "", "alternatives": []}
        if not _clean_query(result["search_queries"].get("primary")):
            result["search_queries"]["primary"] = user_message

        return result

    def _extract_relevant_context(self, current_query: str, history: List[Dict]) -> str:
        """Extract relevant context from conversation history.
        
        Uses smarter selection to include:
        - Recent messages (last 3)
        - Earlier messages with entity overlap
        - Messages that seem to be part of the same topic
        
        Args:
            current_query: The current user message
            history: Full conversation history
        
        Returns:
            Formatted context string
        """
        if not history:
            return "(No previous messages)"
        
        relevant_parts = []
        
        # Extract terms from current query for relevance matching
        query_terms = set(word.lower() for word in current_query.split() if len(word) > 3)
        
        # Always include last 3 messages for immediate context
        recent_count = min(3, len(history))
        recent = history[-recent_count:]
        for msg in recent:
            role = msg.get('role', 'user')
            content = msg.get('content', '')[:400]
            relevant_parts.append(f"{role}: {content}")
        
        # Look for earlier messages with entity/term overlap
        earlier_relevant = []
        for msg in history[:-recent_count]:
            content = msg.get('content', '').lower()
            content_terms = set(word for word in content.split() if len(word) > 3)
            
            # Calculate overlap score
            overlap = len(query_terms & content_terms)
            
            # Include if significant overlap
            if overlap >= 2:
                earlier_relevant.append({
                    'role': msg.get('role', 'user'),
                    'content': msg.get('content', '')[:250],
                    'overlap': overlap
                })
        
        # Sort by relevance and take top 3
        earlier_relevant.sort(key=lambda x: x['overlap'], reverse=True)
        for msg in earlier_relevant[:3]:
            relevant_parts.insert(0, f"[Earlier relevant] {msg['role']}: {msg['content']}")
        
        return "\n".join(relevant_parts[:10])  # Max 10 context entries
    
    def _flatten_entities(self, named_entities: Dict[str, List[str]]) -> List[str]:
        """Flatten named entities dict into a simple list for legacy compatibility.
        
        Args:
            named_entities: Dict with entity categories as keys
        
        Returns:
            Flat list of all entities
        """
        all_entities = []
        for category, entities in named_entities.items():
            if isinstance(entities, list):
                all_entities.extend(entities)
        return list(set(all_entities))  # Deduplicate
    
    async def plan(
        self,
        analysis: Dict[str, Any],
        available_sources: List[Dict[str, Any]]
    ) -> AgentPlan:
        """Phase 2: Create execution plan with optimized queries.
        
        Uses the enhanced analysis to create targeted search tasks
        with entity-optimized queries.
        
        Args:
            analysis: Result from analyze phase
            available_sources: List of available data sources
        
        Returns:
            AgentPlan with tasks to execute
        """
        sources_str = "\n".join([
            f"- {s.get('id', 'unknown')}: {s.get('display_name', '')} (type: {s.get('type', '')})"
            for s in available_sources
        ])

        # Build the list of allowed task types for this tenant, minus the search
        # types that no available source can serve (e.g. no cloud storage synced)
        self._unavailable_task_types = self.unavailable_task_types(available_sources)
        unavailable_values = sorted(t.value for t in self._unavailable_task_types)
        enabled_types = [
            t for t in ToolGate.get_enabled_task_types() if t not in unavailable_values
        ]
        enabled_types_str = ", ".join(enabled_types)

        prompt = self._get_prompt("plan").format(
            analysis=json.dumps(analysis, indent=2),
            available_sources=sources_str
        )

        # Inject tool availability constraint into the prompt
        prompt += f"\n\n**IMPORTANT: You may ONLY use these task types: [{enabled_types_str}]. Do NOT create tasks with any other type.**"
        if unavailable_values:
            prompt += (
                f"\n**The user has no source behind {', '.join(unavailable_values)}: "
                "those searches would find nothing, so do not plan them.**"
            )

        result = await self._call_llm(prompt, OrchestratorPhase.PLAN)
        
        # Parse tasks
        tasks = []
        for task_data in result.get("tasks", []):
            try:
                # Get query - prefer optimized queries from analysis if task query is generic
                raw_query = task_data.get("query", "")
                query = _clean_query(raw_query)
                if raw_query and query != " ".join(str(raw_query).split()):
                    self._degrade("plan_query_repaired")
                if not query:
                    self._degrade("plan_query_empty")
                if not query or query == analysis.get("intent_summary", ""):
                    # Use the primary search query from analysis if available
                    search_queries = analysis.get("search_queries", {}) or {}
                    query = (_clean_query(search_queries.get("primary"))
                             or _clean_query(analysis.get("intent_summary"))
                             or self._user_message)
                
                task = TaskDefinition(
                    id=task_data.get("id", f"task_{len(tasks)}"),
                    type=TaskType(task_data.get("type", "search_all")),
                    query=query,
                    sources=task_data.get("sources", []),
                    priority=task_data.get("priority", 1),
                    depends_on=task_data.get("depends_on", []),
                    max_results=task_data.get("max_results", 10),
                    context_hint=task_data.get("context_hint")
                )
                tasks.append(task)
            except Exception as e:
                logger.warning(f"[req={self.req_id}] Failed to parse task: {e}")

        # The LLM may still plan a cloud/personal search it was told not to
        tasks = self._drop_unavailable_tasks(tasks, "plan")

        # If no tasks were created, create smart default tasks based on analysis
        if not tasks:
            self._degrade("plan_default_tasks")
            tasks = self._create_default_tasks(analysis)

        # Anchor: also search the user's own words. Rewritten queries lose names,
        # switch language or leak syntax; the plain question is what fast mode searches.
        question = (self._user_message or "").strip()
        if settings.agent_anchor_raw_query and question and not any(
            t.query.strip().lower() == question.lower() for t in tasks
        ):
            tasks.append(TaskDefinition(
                id="anchor_user_question",
                type=TaskType.SEARCH_ALL,
                query=question[:1000],
                priority=1,
                max_results=10
            ))
        
        # Defense in depth: filter out any disabled task types the LLM may have planned
        tasks = ToolGate.filter_tasks(tasks)
        
        # If filtering removed everything, re-create defaults
        if not tasks:
            tasks = self._create_default_tasks(analysis)
            tasks = ToolGate.filter_tasks(tasks)
        
        # Safety net: ensure at least one document search task exists
        # The LLM planner might create only web search tasks, but we should
        # always check ingested documents too
        doc_search_types = {TaskType.SEARCH_PROFILE, TaskType.SEARCH_CLOUD, 
                          TaskType.SEARCH_PERSONAL, TaskType.SEARCH_ALL}
        has_doc_search = any(t.type in doc_search_types for t in tasks)
        
        if not has_doc_search:
            primary_query = (
                (analysis.get("search_queries", {}) or {}).get("primary")
                or analysis.get("intent_summary")
                or self._user_message
            )
            logger.info(f"[req={self.req_id}] No document search tasks in plan - adding SEARCH_ALL as safety net")
            tasks.append(TaskDefinition(
                id="safety_doc_search",
                type=TaskType.SEARCH_ALL,
                query=primary_query,
                priority=1,
                max_results=10
            ))
        
        return AgentPlan(
            intent_summary=result.get("intent_summary", analysis.get("intent_summary", "")),
            reasoning=result.get("reasoning", ""),
            strategy=result.get("strategy", "parallel"),
            tasks=tasks,
            success_criteria=result.get("success_criteria", "Find relevant information"),
            max_iterations=result.get("max_iterations", 2)
        )
    
    def _create_default_tasks(self, analysis: Dict[str, Any]) -> List[TaskDefinition]:
        """Create default search tasks based on analysis when planning fails.
        
        IMPORTANT: Always includes at least one document search task to check
        ingested documents, regardless of source_priority. This prevents the 
        common issue where the LLM classifies a query as "web" but the answer
        is actually in the user's ingested documents.
        
        Args:
            analysis: The analysis result
        
        Returns:
            List of default TaskDefinition objects
        """
        tasks = []
        search_queries = analysis.get("search_queries", {}) or {}
        primary_query = (
            _clean_query(search_queries.get("primary"))
            or _clean_query(analysis.get("intent_summary"))
            or self._user_message
        )
        alternatives = search_queries.get("alternatives", [])
        source_priority = analysis.get("source_priority", "profile")
        
        # Primary search based on priority source
        source_type_map = {
            "profile": TaskType.SEARCH_PROFILE,
            "personal": TaskType.SEARCH_PERSONAL,
            "cloud": TaskType.SEARCH_CLOUD,
            "web": TaskType.WEB_SEARCH
        }
        
        primary_type = source_type_map.get(source_priority, TaskType.SEARCH_ALL)
        if primary_type in self._unavailable_task_types:
            # e.g. "cloud" priority while nothing was ever synced: search what exists
            primary_type = TaskType.SEARCH_ALL

        tasks.append(TaskDefinition(
            id="default_primary",
            type=primary_type,
            query=primary_query,
            priority=1,
            max_results=10
        ))
        
        # ALWAYS include a document search if the primary task is web-only
        # This ensures ingested documents are always consulted
        if primary_type == TaskType.WEB_SEARCH:
            tasks.append(TaskDefinition(
                id="default_doc_search",
                type=TaskType.SEARCH_ALL,
                query=primary_query,
                priority=1,
                max_results=10
            ))
        
        # Add alternative query search if available
        if alternatives and len(alternatives) > 0:
            tasks.append(TaskDefinition(
                id="default_alternative",
                type=TaskType.SEARCH_ALL,
                query=alternatives[0],
                priority=2,
                max_results=5
            ))
        
        # Add web search for complex queries or external entities
        if analysis.get("requires_multi_hop") or source_priority == "web":
            # Only add web search if not already the primary task
            if primary_type != TaskType.WEB_SEARCH:
                tasks.append(TaskDefinition(
                    id="default_web",
                    type=TaskType.WEB_SEARCH,
                    query=primary_query,
                    priority=3,
                    max_results=5
                ))
        
        return tasks
    
    async def evaluate(
        self,
        plan: AgentPlan,
        results: List[WorkerResult],
        iteration: int = 1
    ) -> EvaluationDecision:
        """Phase 3: Evaluate results and decide next steps.
        
        Args:
            plan: The execution plan
            results: Results from worker executions
            iteration: Current iteration number
        
        Returns:
            EvaluationDecision with next steps
        """
        # Summarize results
        results_summary = []
        for r in results:
            summary = {
                "task_id": r.task_id,
                "task_type": r.task_type,
                "query": r.query,
                "success": r.success,
                "documents_found": len(r.documents_found),
                "web_links_found": len(r.web_links_found),
                "quality": r.result_quality,
            }
            # Add document excerpts
            if r.documents_found:
                summary["top_documents"] = [
                    {"title": d.title, "excerpt": d.excerpt[:200]}
                    for d in r.documents_found[:3]
                ]
            if r.web_links_found:
                summary["top_links"] = [
                    {"title": l.title, "url": l.url}
                    for l in r.web_links_found[:3]
                ]
            results_summary.append(summary)
        
        prompt = self._get_prompt("evaluate").format(
            intent=plan.intent_summary,
            success_criteria=plan.success_criteria,
            results_summary=json.dumps(results_summary, indent=2)
        )
        
        result = await self._call_llm(prompt, OrchestratorPhase.EVALUATE)
        phase = "initial" if iteration == 1 else ("refinement" if iteration == 2 else "final")

        if result.get("parse_error"):
            # Decide from the results instead of defaulting to "sufficient".
            found = sum(len(r.documents_found) + len(r.web_links_found) for r in results)
            decision = "sufficient" if found else "cannot_answer"
            self._degrade(f"evaluate_fallback_{decision}")
            return EvaluationDecision(
                phase=phase,
                findings_summary="",
                gaps_identified=[],
                decision=decision,
                follow_up_tasks=[],
                reasoning="Evaluation output could not be parsed; decided from the number of results found.",
                confidence=0.0,
            )

        # Parse follow-up tasks
        follow_up_tasks = []
        for task_data in result.get("follow_up_tasks", []):
            try:
                task = TaskDefinition(
                    id=task_data.get("id", f"followup_{len(follow_up_tasks)}"),
                    type=TaskType(task_data.get("type", "search_all")),
                    query=task_data.get("query", ""),
                    sources=task_data.get("sources", []),
                    priority=task_data.get("priority", 1),
                    depends_on=task_data.get("depends_on", []),
                    max_results=task_data.get("max_results", 10),
                    context_hint=task_data.get("context_hint")
                )
                follow_up_tasks.append(task)
            except Exception as e:
                logger.warning(f"[req={self.req_id}] Failed to parse follow-up task: {e}")
        
        # Filter out disabled task types and sourceless search types from follow-up tasks
        follow_up_tasks = ToolGate.filter_tasks(follow_up_tasks)
        follow_up_tasks = self._drop_unavailable_tasks(follow_up_tasks, "evaluate")

        decision = str(result.get("decision") or "sufficient").strip().lower()
        if decision not in ("sufficient", "need_refinement", "need_expansion", "cannot_answer"):
            self._degrade("evaluate_invalid_decision")
            decision = ("cannot_answer" if "cannot" in decision
                        else "need_refinement" if ("refine" in decision or "insufficient" in decision)
                        else "sufficient")
        try:
            confidence = min(1.0, max(0.0, float(result.get("confidence", 0.5))))
        except (TypeError, ValueError):
            self._degrade("evaluate_invalid_confidence")
            confidence = 0.5
        gaps = result.get("gaps_identified", [])

        return EvaluationDecision(
            phase=phase,
            findings_summary=str(result.get("findings_summary", "") or ""),
            gaps_identified=[str(g) for g in gaps] if isinstance(gaps, list) else [],
            decision=decision,
            follow_up_tasks=follow_up_tasks,
            reasoning=str(result.get("reasoning", "") or ""),
            confidence=confidence
        )
    
    async def synthesize(
        self,
        user_message: str,
        all_results: List[WorkerResult],
        language: Optional[str] = None
    ) -> str:
        """Phase 4: Generate final answer.
        
        Args:
            user_message: Original user message
            all_results: All results from all iterations
            language: UI language code (e.g. "en", "de") for response language
        
        Returns:
            Final answer string
        """
        # Compile all results
        all_info = []
        
        for r in all_results:
            if r.documents_found:
                for doc in r.documents_found:
                    all_info.append({
                        "type": "document",
                        "title": doc.title,
                        "source": doc.source_type,
                        "content": doc.full_content or doc.excerpt,
                        "score": doc.similarity_score
                    })
            if r.web_links_found:
                for link in r.web_links_found:
                    all_info.append({
                        "type": "web",
                        "title": link.title,
                        "url": link.url,
                        "content": link.fetched_content or link.excerpt
                    })
        
        # Sort by relevance
        all_info.sort(key=lambda x: x.get("score", 0), reverse=True)
        
        # Format for prompt - limit total content size to avoid context overflow
        # Estimate ~4 chars per token, aim for max ~10k tokens for results
        max_content_chars = 40000
        current_chars = 0
        limited_info = []
        
        for info in all_info:
            content = info.get("content", "")
            # Truncate individual content if needed
            if len(content) > 2000:
                content = content[:2000] + "..."
                info = {**info, "content": content}
            
            entry_chars = len(json.dumps(info))
            if current_chars + entry_chars > max_content_chars:
                logger.info(f"[req={self.req_id}] Truncating synthesis context: {len(limited_info)} of {len(all_info)} items used")
                break
            limited_info.append(info)
            current_chars += entry_chars
        
        # Format for prompt
        if limited_info:
            results_str = json.dumps(limited_info, indent=2)
        else:
            results_str = "No relevant information was found in the searches."
        
        prompt = self._get_prompt("synthesize").format(
            user_message=user_message,
            all_results=results_str
        )
        
        # Inject language instruction if non-English language is specified
        if language and language.lower() != "en":
            lang_names = {"de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "nl": "Dutch"}
            lang_name = lang_names.get(language.lower(), language)
            prompt += f"\n\n**CRITICAL LANGUAGE REQUIREMENT: You MUST write your entire response in {lang_name}. The user's interface is set to {lang_name} and the question was asked in {lang_name}. All text, headings, explanations, and summaries must be in {lang_name}. Only keep proper nouns, document titles, and citation references in their original language.**"
        
        try:
            result = await self._call_llm(prompt, OrchestratorPhase.SYNTHESIZE, expect_json=False)
            response_text = result.get("response")
            
            # Check for empty or whitespace-only response
            if response_text and response_text.strip():
                return response_text
            
            logger.warning(f"[req={self.req_id}] LLM returned empty response during synthesis, using fallback")
            self._degrade("synthesize_empty_fallback")
            
        except Exception as e:
            logger.error(f"[req={self.req_id}] Synthesis LLM call failed: {e}")
            self._degrade("synthesize_failed_fallback")
        
        # Fallback: Generate a basic response from the found documents
        if limited_info:
            fallback_parts = ["Based on the search results, here's what I found:\n"]
            for i, info in enumerate(limited_info[:5], 1):
                title = info.get("title", "Untitled")
                content = info.get("content", "")[:500]
                source = info.get("source", info.get("type", "unknown"))
                fallback_parts.append(f"**{i}. {title}** (Source: {source})\n{content}\n")
            return "\n".join(fallback_parts)
        
        return "I was unable to generate a response. The search found relevant documents but synthesis failed. Please try again."
    
    def _format_results_for_prompt(self, results: List[WorkerResult]) -> str:
        """Format worker results for inclusion in prompts.
        
        Args:
            results: List of worker results
        
        Returns:
            Formatted string
        """
        lines = []
        for r in results:
            lines.append(f"Task {r.task_id} ({r.task_type}):")
            lines.append(f"  Query: {r.query}")
            lines.append(f"  Documents: {len(r.documents_found)}")
            if r.documents_found:
                for d in r.documents_found[:3]:
                    lines.append(f"    - {d.title}: {d.excerpt[:100]}...")
            lines.append(f"  Web links: {len(r.web_links_found)}")
            if r.web_links_found:
                for l in r.web_links_found[:3]:
                    lines.append(f"    - {l.title}: {l.url}")
        return "\n".join(lines)
