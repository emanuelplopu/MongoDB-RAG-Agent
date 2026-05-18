"""Shared LLM integration helper for Strategy OS node executors."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from litellm import acompletion

from backend.core.model_roles import ModelRoleConfig, ModelRoleRegistry
from backend.core.llm_providers import LLMProvider

logger = logging.getLogger(__name__)


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

        try:
            response = await acompletion(**params)
            text = response.choices[0].message.content or ""
            tokens = response.usage.total_tokens if response.usage else self._estimate_tokens(text)
            return (text, tokens)
        except Exception as e:
            logger.error(f"NodeLLMHelper LLM call failed: role={role}, model={litellm_model}, error={e}")
            raise

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
