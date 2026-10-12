"""Request options for Ollama calls made through LiteLLM.

Thinking-capable models (gemma4, qwen3, ...) reason before they answer unless told
not to. Through LiteLLM's Ollama adapter that reasoning is invisible: it is dropped
from the response but counts against ``max_tokens``, so a JSON phase can run out of
budget before the JSON starts and short calls (query refinement, summaries) come back
empty. Grammar-constrained JSON (``format=json``) removes parse failures in the JSON
phases, and an explicit ``num_ctx`` keeps the server from loading models with its
global default context (which decides how much VRAM each model takes and whether two
models fit on the GPU together).

All options are configured in ``backend.core.config`` (``agent_ollama_*``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Model name -> supports thinking; filled lazily from /api/show, failures are not cached.
_thinking_capable: Dict[str, bool] = {}


def is_ollama_model(model_string: str, provider: str = "") -> bool:
    """True if a LiteLLM model string or provider name refers to Ollama."""
    return provider.lower() == "ollama" or model_string.startswith("ollama/")


def thinking_enabled(phase: str) -> bool:
    """True if hidden reasoning is allowed for this call site (``agent_ollama_think_phases``)."""
    phases = {p.strip() for p in settings.agent_ollama_think_phases.split(",") if p.strip()}
    return phase in phases or "all" in phases


async def supports_thinking(model_string: str) -> bool:
    """Whether the Ollama model reasons before answering (``thinking`` capability)."""
    name = model_string.split("/", 1)[1] if model_string.startswith("ollama/") else model_string
    if name not in _thinking_capable:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{settings.ollama_base_url.rstrip('/')}/api/show", json={"model": name})
                response.raise_for_status()
                _thinking_capable[name] = "thinking" in (response.json().get("capabilities") or [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Could not read the capabilities of Ollama model %s: %s", name, exc)
            return False
    return _thinking_capable[name]


def think_budget(applied: Dict[str, Any]) -> int:
    """Extra output tokens to allow when :func:`apply_ollama_options` enabled thinking."""
    return settings.agent_ollama_think_budget if applied.get("think") else 0


async def apply_ollama_options(llm_params: Dict[str, Any], phase: str, *, json_mode: bool = False) -> Dict[str, Any]:
    """Add context size, thinking switch, JSON mode and repeat penalty to an Ollama request.

    Args:
        llm_params: LiteLLM ``acompletion`` keyword arguments; updated in place.
        phase: Call site name (``analyze``, ``plan``, ``evaluate``, ``synthesize``,
            ``fast_synthesize``, ``summarize``, ``refine_query``).
        json_mode: The caller parses the reply as JSON.

    Returns:
        The options that were applied, for traces and telemetry.
    """
    applied: Dict[str, Any] = {}
    if settings.agent_ollama_num_ctx > 0:
        llm_params["num_ctx"] = settings.agent_ollama_num_ctx
        applied["num_ctx"] = settings.agent_ollama_num_ctx
    if await supports_thinking(llm_params["model"]):
        think = thinking_enabled(phase)
        if not think:
            # LiteLLM maps reasoning_effort to Ollama's `think`; anything but low/medium/high means off.
            llm_params["reasoning_effort"] = "none"
        applied["think"] = think
    if json_mode and settings.agent_ollama_json_mode:
        llm_params["response_format"] = {"type": "json_object"}
        applied["json_mode"] = True
    # Penalties corrupt JSON keys and names that must be repeated verbatim.
    llm_params["repeat_penalty"] = 1.0 if json_mode else settings.agent_ollama_repeat_penalty
    applied["repeat_penalty"] = llm_params["repeat_penalty"]
    return applied
