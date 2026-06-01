"""Shared LLM integration helper for Strategy OS node executors.

In addition to the original model-resolution / completion / JSON-extraction
helpers, this module exposes a *capture context* (Task 90 / T2) that
records every LLM invocation made via :meth:`NodeLLMHelper.complete`
into an :class:`~backend.agent.strategy.llm_call_store.LLMCallStore`.

The capture is fully transparent to node executors:

* When :class:`backend.agent.strategy.strategy_runner.StrategyRunner`
  is configured with an ``llm_call_store`` it pushes a fresh
  :class:`LLMCaptureContext` (via :func:`set_capture_context`) into a
  :class:`contextvars.ContextVar` before each node runs and pops it
  again afterwards.
* :meth:`NodeLLMHelper.complete` reads the active context, persists a
  :class:`~backend.agent.strategy.llm_call_store.LLMCallDoc`, and
  appends the resulting ``call_id`` to the context's ``call_ids``
  list. The runner then extends ``state.llm_call_ids`` with that
  list so the run trace carries cross-references to every captured
  prompt/response.

The capture path is *best-effort*: store failures are logged and
swallowed, the LLM result is always returned to the caller unchanged,
and absence of a context (or store) makes capture a no-op. Node
executors require zero code changes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Optional

from litellm import acompletion

from backend.core.model_roles import ModelRoleConfig, ModelRoleRegistry
from backend.core.llm_providers import LLMProvider
from backend.agent.strategy.llm_call_store import LLMCallDoc, LLMCallStore

logger = logging.getLogger(__name__)

__all__ = [
    "NodeLLMHelper",
    "LLMCaptureContext",
    "set_capture_context",
    "reset_capture_context",
    "get_capture_context",
]


# ════════════════════════════════════════════════════════════════════════════
# Capture context (Task 90 / T2)
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class LLMCaptureContext:
    """Per-node telemetry capture envelope for LLM invocations.

    Pushed into :data:`_capture_ctx` by
    :class:`~backend.agent.strategy.strategy_runner.StrategyRunner`
    immediately before a node executes. Read by
    :meth:`NodeLLMHelper.complete` to persist the call and to record
    the resulting ``call_id`` for cross-reference on the run state.

    Attributes:
        trace_id: Strategy run trace identifier.
        node_id: Stable DAG node identifier.
        node_type: Node type token (``synthesize`` etc.).
        store: Backend used to persist the constructed
            :class:`LLMCallDoc`. ``None`` disables capture entirely.
        call_ids: Mutable list that accumulates persisted call ids
            (in invocation order) so the runner can copy them onto
            ``state.llm_call_ids`` once the node returns.
    """

    trace_id: str
    node_id: str
    node_type: str
    store: Optional[LLMCallStore] = None
    call_ids: list[str] = field(default_factory=list)


#: ContextVar holding the active capture context for the current
#: asyncio task. ``None`` means "no capture" (the default; preserves
#: backwards compatibility for code paths that construct
#: :class:`NodeLLMHelper` directly outside a strategy run).
_capture_ctx: ContextVar[Optional[LLMCaptureContext]] = ContextVar(
    "strategy_llm_capture_ctx", default=None
)


def set_capture_context(ctx: Optional[LLMCaptureContext]) -> Token:
    """Install ``ctx`` as the active capture context.

    Returns:
        A reset token suitable for :func:`reset_capture_context`.
    """
    return _capture_ctx.set(ctx)


def reset_capture_context(token: Token) -> None:
    """Restore the capture context to its prior value."""
    _capture_ctx.reset(token)


def get_capture_context() -> Optional[LLMCaptureContext]:
    """Return the active capture context, or ``None``."""
    return _capture_ctx.get()


def _extract_prompts(messages: list[dict]) -> tuple[Optional[str], str]:
    """Split ``messages`` into (system_prompt, user_prompt) for capture.

    The full system prompt is the concatenation of all ``system``
    messages (separated by ``\\n\\n``); the user prompt is every other
    message rendered as ``ROLE:\\n<content>`` so multi-turn dialogues
    are preserved verbatim. No truncation is performed — telemetry
    must capture the complete prompt that was actually sent.
    """
    system_parts: list[str] = []
    other_parts: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "")
        content = msg.get("content", "") or ""
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:  # noqa: BLE001 - best-effort serialise
                content = str(content)
        if role == "system":
            system_parts.append(content)
        elif len(messages) == 1 or role == "":
            other_parts.append(content)
        else:
            other_parts.append(f"{role.upper()}:\n{content}")
    system_prompt = "\n\n".join(system_parts) if system_parts else None
    user_prompt = "\n\n".join(other_parts)
    return system_prompt, user_prompt


class NodeLLMHelper:
    """
    Wraps LLM client access with model role resolution, token tracking,
    and consistent error handling for Strategy OS node executors.

    Usage:
        helper = NodeLLMHelper(model_registry=registry, settings=settings)
        text, tokens = await helper.complete("synthesizer_fast", messages, node_config)
        data, tokens = await helper.complete_json("worker", messages, node_config)
    """

    def __init__(
        self,
        model_registry: Optional[ModelRoleRegistry] = None,
        settings=None,  # BackendSettings
    ):
        self.model_registry = model_registry
        self.settings = settings

    def resolve_model(self, role: str) -> tuple[str, str, Optional[str]]:
        """
        Resolve a role to (provider, model, api_base).

        Uses ModelRoleRegistry.resolve_with_fallback(role).
        Returns ("provider_string", "model_string", "api_base_or_none")

        If no registry, returns default ("ollama", "llama3.1:8b", "http://localhost:11434")
        """
        if not self.model_registry:
            return ("ollama", "llama3.1:8b", "http://localhost:11434")

        config: ModelRoleConfig = self.model_registry.resolve_with_fallback(role)
        provider = config.provider
        model = config.model

        # Determine api_base based on provider
        api_base: Optional[str] = None
        if provider == "ollama":
            if self.settings:
                api_base = getattr(self.settings, "ollama_base_url", "http://host.docker.internal:11434")
            else:
                api_base = "http://host.docker.internal:11434"
        elif provider == "openai_compatible":
            if self.settings:
                api_base = getattr(self.settings, "llm_base_url", None)

        return (provider, model, api_base)

    def _get_api_key(self, provider: str) -> Optional[str]:
        """Get the API key for a given provider from settings."""
        if not self.settings:
            return None
        if provider == "ollama":
            return None
        return self.settings.get_api_key_for_provider(provider)

    def _build_litellm_model_string(self, provider: str, model: str) -> str:
        """Build the LiteLLM-format model string from provider and model name."""
        if provider == LLMProvider.OPENAI.value or provider == "openai":
            return model
        elif provider == LLMProvider.GOOGLE.value or provider == "google":
            if not model.startswith("gemini/"):
                return f"gemini/{model}"
            return model
        elif provider == LLMProvider.ANTHROPIC.value or provider == "anthropic":
            if not model.startswith("anthropic/"):
                return f"anthropic/{model}"
            return model
        elif provider == LLMProvider.OLLAMA.value or provider == "ollama":
            if not model.startswith("ollama/"):
                return f"ollama/{model}"
            return model
        elif provider == LLMProvider.OPENAI_COMPATIBLE.value or provider == "openai_compatible":
            if not model.startswith("openai/"):
                return f"openai/{model}"
            return model
        return model

    async def complete(
        self,
        role: str,
        messages: list[dict],
        node_config: Optional[dict] = None,
    ) -> tuple[str, int]:
        """
        Make an LLM completion call for a given role.

        Args:
            role: Model role (e.g., "synthesizer_fast", "worker", "judge")
            messages: List of message dicts [{"role": "system", "content": ...}, ...]
            node_config: Optional node config for overrides (temperature, max_tokens, etc.)

        Returns:
            (response_text, tokens_used) tuple

        Raises:
            Exception on LLM call failure (caller should handle)
        """
        provider, model, api_base = self.resolve_model(role)
        litellm_model = self._build_litellm_model_string(provider, model)
        api_key = self._get_api_key(provider)

        # Extract overrides from node_config
        cfg = node_config or {}
        temperature = cfg.get("temperature", 0.7)
        max_tokens = cfg.get("max_tokens", 2000)
        seed = cfg.get("seed", None)

        # Build call params
        params: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": temperature,
        }

        if api_base:
            params["api_base"] = api_base
        if api_key:
            params["api_key"] = api_key

        # Handle max_tokens vs max_completion_tokens for newer OpenAI models
        model_lower = litellm_model.lower()
        needs_max_completion_tokens = any(
            prefix in model_lower for prefix in ["gpt-5", "gpt-4o", "gpt-4.1", "o1", "o3", "o4"]
        )
        if needs_max_completion_tokens:
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens

        if seed is not None:
            params["seed"] = seed

        # Apply request timeout for local models
        if provider == "ollama" and self.settings:
            timeout = getattr(self.settings, "agent_llm_request_timeout", 1800)
            params["timeout"] = timeout

        logger.debug(f"NodeLLMHelper.complete: role={role}, model={litellm_model}, provider={provider}")

        # Telemetry capture (Task 90 / T2): a no-op when no capture
        # context is active or the bound store is ``None``.
        capture = get_capture_context()
        capture_active = capture is not None and capture.store is not None
        start_perf = time.perf_counter()
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        total_tokens: Optional[int] = None
        text: str = ""
        success: bool = True
        error_message: Optional[str] = None

        try:
            response = await acompletion(**params)
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage is not None:
                total_tokens = getattr(usage, "total_tokens", None)
                prompt_tokens = getattr(usage, "prompt_tokens", None)
                completion_tokens = getattr(usage, "completion_tokens", None)
            tokens = total_tokens if total_tokens is not None else self._estimate_tokens(text)
            return (text, tokens)
        except Exception as e:
            success = False
            error_message = f"{type(e).__name__}: {e}"
            logger.error(f"NodeLLMHelper LLM call failed: role={role}, model={litellm_model}, error={e}")
            raise
        finally:
            if capture_active:
                latency_ms = (time.perf_counter() - start_perf) * 1000.0
                await self._record_call(
                    capture=capture,  # type: ignore[arg-type]
                    role=role,
                    provider=provider,
                    model=model,
                    messages=messages,
                    response_text=text,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    success=success,
                    error=error_message,
                )

    async def _record_call(
        self,
        *,
        capture: LLMCaptureContext,
        role: str,
        provider: str,
        model: str,
        messages: list[dict],
        response_text: str,
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        total_tokens: Optional[int],
        latency_ms: float,
        temperature: Optional[float],
        max_tokens: Optional[int],
        success: bool,
        error: Optional[str],
    ) -> None:
        """Persist one :class:`LLMCallDoc` for the active capture context.

        Best-effort: any failure constructing or saving the document is
        logged at warning level and swallowed so a telemetry write
        never breaks the actual strategy run. The persisted ``call_id``
        is appended to ``capture.call_ids`` so the runner can
        cross-reference the call from ``state.llm_call_ids``.
        """
        store = capture.store
        if store is None:  # pragma: no cover - defensive
            return
        try:
            system_prompt, user_prompt = _extract_prompts(messages)
            doc = LLMCallDoc(
                trace_id=capture.trace_id,
                node_id=capture.node_id,
                node_type=capture.node_type,
                model=model,
                provider=provider,
                model_role=role,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                assistant_response=response_text or "",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=max(0.0, float(latency_ms)),
                temperature=temperature,
                max_tokens=max_tokens,
                success=success,
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 - never break the run
            logger.warning(
                "LLM-call telemetry: failed to build LLMCallDoc "
                "(node_id=%s, trace_id=%s): %s",
                capture.node_id,
                capture.trace_id,
                exc,
            )
            return
        try:
            await store.save(doc)
        except Exception as exc:  # noqa: BLE001 - best-effort sink
            logger.warning(
                "LLM-call telemetry: store.save failed "
                "(node_id=%s, trace_id=%s): %s",
                capture.node_id,
                capture.trace_id,
                exc,
            )
            return
        try:
            capture.call_ids.append(doc.call_id)
        except Exception:  # noqa: BLE001 - defensive
            pass

    async def complete_json(
        self,
        role: str,
        messages: list[dict],
        node_config: Optional[dict] = None,
    ) -> tuple[dict | list, int]:
        """
        Make an LLM call expecting JSON response.

        Same as complete() but:
        1. Appends "Respond with valid JSON only." to the last user message
        2. Parses response as JSON
        3. Handles markdown code blocks (```json ... ```)
        4. Returns (parsed_data, tokens_used)

        Raises ValueError if response is not valid JSON.
        """
        # Clone messages to avoid mutating the caller's list
        augmented = [dict(m) for m in messages]

        # Append JSON instruction to the last user message
        for msg in reversed(augmented):
            if msg.get("role") == "user":
                msg["content"] = msg.get("content", "") + "\n\nRespond with valid JSON only."
                break
        else:
            # No user message found; append as a new user message
            augmented.append({"role": "user", "content": "Respond with valid JSON only."})

        text, tokens = await self.complete(role, augmented, node_config)

        parsed = self._extract_json(text)
        if parsed is None:
            raise ValueError(f"LLM response is not valid JSON: {text[:300]}")

        return (parsed, tokens)

    @staticmethod
    def _extract_json(text: str) -> Any:
        """
        Extract JSON from text, handling markdown code blocks.

        Tries:
        1. Direct json.loads(text)
        2. Extract from ```json ... ``` blocks
        3. Extract from ``` ... ``` blocks
        4. Find first { or [ and try parsing from there
        """
        text = text.strip()

        # 1. Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. ```json ... ``` blocks
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 3. ``` ... ``` blocks
        match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 4. Find first { or [ and try parsing from there
        for i, ch in enumerate(text):
            if ch in ("{", "["):
                try:
                    return json.loads(text[i:])
                except json.JSONDecodeError:
                    pass

        return None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        Rough token estimation (chars / 4).
        Used when actual token count not available from response.
        """
        return max(1, len(text) // 4)
