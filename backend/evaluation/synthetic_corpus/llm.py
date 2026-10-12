"""LLM access: OpenAI Responses API with structured outputs, caching and budgets.

Every call returns a validated Pydantic object. Responses are cached on disk by
a hash of (model, effort, prompts, schema), so interrupted runs resume without
paying twice and reruns are reproducible. Usage is tracked per model and checked
against :class:`~.config.BudgetConfig` before each request.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLMConfig, LLMRoleConfig

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Role = Literal["orchestrator", "generator"]


class BudgetExceeded(RuntimeError):
    """Raised when a request would exceed the configured budget."""


class LLMError(RuntimeError):
    """Raised when a request keeps failing after all retries."""


@dataclass
class ModelUsage:
    """Accumulated usage for one model."""

    requests: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0


@dataclass
class UsageTracker:
    """Usage across all models, with optional price estimates."""

    per_model: dict[str, ModelUsage] = field(default_factory=dict)

    def get(self, model: str) -> ModelUsage:
        return self.per_model.setdefault(model, ModelUsage())

    @property
    def total_requests(self) -> int:
        return sum(u.requests for u in self.per_model.values())

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.per_model.values())

    def report(self, prices: dict[str, Any] | None = None) -> dict[str, Any]:
        """Usage summary; adds USD estimates for models with configured prices."""
        out: dict[str, Any] = {"models": {}, "total_requests": self.total_requests}
        total_cost = 0.0
        for model, u in self.per_model.items():
            entry = u.__dict__.copy()
            price = (prices or {}).get(model)
            if price is not None:
                cost = u.input_tokens / 1e6 * price.input_per_1m + u.output_tokens / 1e6 * price.output_per_1m
                entry["estimated_usd"] = round(cost, 4)
                total_cost += cost
            out["models"][model] = entry
        if prices:
            out["estimated_usd_total"] = round(total_cost, 4)
        return out


class LLMClient(Protocol):
    """What the pipeline needs from an LLM backend."""

    usage: UsageTracker

    async def structured(self, role: Role, system: str, user: str, schema: type[T], *, tag: str) -> T:
        """Return a validated ``schema`` instance for the prompts."""
        ...


def _cache_key(role_cfg: LLMRoleConfig, system: str, user: str, schema: type[BaseModel]) -> str:
    payload = json.dumps(
        {
            "model": role_cfg.model,
            "effort": role_cfg.reasoning_effort,
            "system": system,
            "user": user,
            "schema": schema.model_json_schema(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OpenAILLMClient:
    """Async OpenAI Responses API client with retries, cache and budget checks."""

    def __init__(self, cfg: LLMConfig, api_key: str, cache_dir: Optional[Path], log_path: Optional[Path] = None):
        from openai import AsyncOpenAI

        self.cfg = cfg
        self.usage = UsageTracker()
        self._client = AsyncOpenAI(api_key=api_key, base_url=cfg.base_url, max_retries=0)
        self._sem = asyncio.Semaphore(cfg.concurrency)
        self._cache_dir = cache_dir if cfg.cache_responses else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_path
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def _role_cfg(self, role: Role) -> LLMRoleConfig:
        return self.cfg.orchestrator if role == "orchestrator" else self.cfg.generator

    def _check_budget(self) -> None:
        b = self.cfg.budget
        if b.max_requests is not None and self.usage.total_requests >= b.max_requests:
            raise BudgetExceeded(f"request budget reached ({b.max_requests})")
        if b.max_output_tokens_total is not None and self.usage.total_output_tokens >= b.max_output_tokens_total:
            raise BudgetExceeded(f"output token budget reached ({b.max_output_tokens_total})")

    def _log(self, record: dict[str, Any]) -> None:
        if self._log_path:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def structured(self, role: Role, system: str, user: str, schema: type[T], *, tag: str) -> T:
        """Call the model for ``role`` and parse the answer into ``schema``.

        Args:
            role: Which configured model to use.
            system: System instructions.
            user: User content.
            schema: Pydantic model describing the expected JSON.
            tag: Short label for logs (e.g. ``plan:case_01``).

        Returns:
            The validated object.

        Raises:
            BudgetExceeded: If the budget is used up.
            LLMError: If all attempts fail.
        """
        from openai import (
            APIConnectionError,
            APITimeoutError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )

        role_cfg = self._role_cfg(role)
        key = _cache_key(role_cfg, system, user, schema)
        cache_file = self._cache_dir / f"{key}.json" if self._cache_dir else None
        if cache_file and cache_file.is_file():
            try:
                obj = schema.model_validate_json(json.loads(cache_file.read_text(encoding="utf-8"))["output"])
                self.usage.get(role_cfg.model).cache_hits += 1
                self._log({"tag": tag, "model": role_cfg.model, "cache": True})
                return obj
            except (ValidationError, KeyError, json.JSONDecodeError):
                logger.warning("ignoring unreadable cache entry %s", cache_file.name)

        last_error: Optional[BaseException] = None
        for attempt in range(self.cfg.max_retries + 1):
            self._check_budget()
            kwargs: dict[str, Any] = {
                "model": role_cfg.model,
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "text_format": schema,
                "max_output_tokens": role_cfg.max_output_tokens,
                "timeout": role_cfg.timeout_s,
            }
            if role_cfg.reasoning_effort:
                kwargs["reasoning"] = {"effort": role_cfg.reasoning_effort}
            started = time.monotonic()
            try:
                async with self._sem:
                    resp = await self._client.responses.parse(**kwargs)
                elapsed = time.monotonic() - started
                usage = self.usage.get(role_cfg.model)
                usage.requests += 1
                usage.seconds += elapsed
                if resp.usage is not None:
                    usage.input_tokens += resp.usage.input_tokens or 0
                    usage.output_tokens += resp.usage.output_tokens or 0
                    details = getattr(resp.usage, "output_tokens_details", None)
                    usage.reasoning_tokens += getattr(details, "reasoning_tokens", 0) or 0
                parsed = resp.output_parsed
                self._log(
                    {
                        "tag": tag,
                        "model": role_cfg.model,
                        "attempt": attempt,
                        "seconds": round(elapsed, 2),
                        "status": getattr(resp, "status", None),
                        "input_tokens": getattr(resp.usage, "input_tokens", None),
                        "output_tokens": getattr(resp.usage, "output_tokens", None),
                    }
                )
                if parsed is None:
                    reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
                    raise LLMError(f"no structured output (status={getattr(resp, 'status', '?')}, reason={reason})")
                if cache_file:
                    cache_file.write_text(
                        json.dumps({"tag": tag, "model": role_cfg.model, "output": parsed.model_dump_json()}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                return parsed
            except BadRequestError:
                raise
            except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError, LLMError, ValidationError) as exc:
                last_error = exc
                wait = self.cfg.retry_backoff_s * (2**attempt)
                logger.warning("%s attempt %d failed (%s); retrying in %.0fs", tag, attempt + 1, type(exc).__name__, wait)
                self._log({"tag": tag, "model": role_cfg.model, "attempt": attempt, "error": f"{type(exc).__name__}: {exc}"[:300]})
                await asyncio.sleep(wait)
        raise LLMError(f"{tag}: all {self.cfg.max_retries + 1} attempts failed: {last_error}")


class FakeLLMClient:
    """Deterministic stand-in for tests: a responder function builds each answer."""

    def __init__(self, responder: Callable[[Role, str, str, type[BaseModel], str], BaseModel]):
        self.usage = UsageTracker()
        self._responder = responder
        self.calls: list[tuple[Role, str, str]] = []

    async def structured(self, role: Role, system: str, user: str, schema: type[T], *, tag: str) -> T:
        self.calls.append((role, tag, schema.__name__))
        self.usage.get(f"fake-{role}").requests += 1
        result = self._responder(role, system, user, schema, tag)
        return schema.model_validate(result.model_dump())
