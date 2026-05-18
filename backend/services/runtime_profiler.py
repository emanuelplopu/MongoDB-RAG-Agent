"""Runtime LLM profiler service (Phase 6 / Task 70).

Runs a fixed suite of standardized prompts against a local LLM and
records empirical performance into the ``runtime_model_profiles``
collection via :class:`RuntimeProfileStore`.

Standardized tests (Blueprint 08 §5):

============================  =====================  =====================
Test name                     Context tokens          Output tokens
============================  =====================  =====================
``tiny_classification``       500                    100
``short_rag``                 2000                   500
``evidence_synthesis``        6000                   1200
``long_context``              32000                  1500
``very_long_context``         64000                  1500
``parallel_workers``          2000 × N=4 concurrent  500 × N
============================  =====================  =====================

The profiler is intentionally side-effect free at import time:
no DB connection, no provider initialisation, no network. The
worker entrypoint (:mod:`backend.workers.runtime_profiler_worker`)
wires in concrete dependencies at runtime.

Token-counting strategy
-----------------------

Standardized prompts are built deterministically by repeating a fixed
~4-character lorem-ipsum-style word ("lorem ") until the desired
*approximate* token count is reached, using the
``≈ 1 token per 4 characters`` heuristic recommended by OpenAI for
mixed English text. Exact token counts depend on the provider
tokenizer; the goal is reproducibility across runs, not exact
parity with a tokenizer's count. Persisted ``context_tokens`` and
``output_tokens`` reflect the *target* counts used to size the
prompt — they are the workload knobs, not after-the-fact
measurements.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from backend.agent.strategy.runtime_profile_store import (
    STANDARD_TEST_NAMES,
    RuntimeModelProfile,
    RuntimeProfileStore,
)

logger = logging.getLogger(__name__)

__all__ = [
    "STANDARD_TEST_SPECS",
    "ParallelWorkersConfig",
    "RuntimeProfiler",
    "build_standard_prompt",
]


# ──────────────────────────────────────────────────────────────────────────────
# Provider-cost extraction (F4 / Task 82)
# ──────────────────────────────────────────────────────────────────────────────

#: LiteLLM/OpenAI-compat keys consulted (in order) when looking for an
#: explicit billing-metadata cost on a provider response. The list is
#: tuples of ``(top-level key, nested key)``; a ``None`` nested key means
#: the value lives at the top level. Best-effort: missing keys are
#: silently skipped, never raised.
_PROVIDER_COST_KEYS: tuple[tuple[str, Optional[str]], ...] = (
    # 1. Direct top-level (custom adapters that pre-compute EUR cost).
    ("cost_eur", None),
    # 2. LiteLLM/OpenAI-compat ``usage`` block, EUR-normalised.
    ("usage", "cost_eur"),
    # 3. LiteLLM ``_hidden_params.response_cost`` convention
    #    (https://docs.litellm.ai/docs/observability/cost_tracking).
    ("_hidden_params", "response_cost"),
)


def _extract_provider_cost(provider_result: dict[str, Any]) -> Optional[float]:
    """Best-effort extraction of an explicit cost from a provider response.

    Probes a fixed set of well-known keys (LiteLLM/OpenAI-compat); the
    first numeric value wins. Never raises: malformed entries are
    skipped and ``None`` is returned when no key matches.

    Args:
        provider_result: Raw dict returned by the provider invoker.

    Returns:
        The cost in EUR as a non-negative float when found, else
        ``None``.
    """
    if not isinstance(provider_result, dict):
        return None
    for top, nested in _PROVIDER_COST_KEYS:
        try:
            value: Any = provider_result.get(top)
            if nested is not None:
                if not isinstance(value, dict):
                    continue
                value = value.get(nested)
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                cost = float(value)
                if cost >= 0.0:
                    return cost
        except Exception:  # noqa: BLE001 - never raise on best-effort path
            continue
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Standardized prompt suite
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _TestSpec:
    """Single standardized test definition."""

    name: str
    context_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ParallelWorkersConfig:
    """Concurrency knobs for the ``parallel_workers`` test."""

    concurrency: int = 4
    base_test: str = "short_rag"


#: Per-test target context/output sizes (Blueprint 08 §5).
STANDARD_TEST_SPECS: dict[str, _TestSpec] = {
    "tiny_classification": _TestSpec("tiny_classification", 500, 100),
    "short_rag": _TestSpec("short_rag", 2000, 500),
    "evidence_synthesis": _TestSpec("evidence_synthesis", 6000, 1200),
    "long_context": _TestSpec("long_context", 32000, 1500),
    "very_long_context": _TestSpec("very_long_context", 64000, 1500),
    # ``parallel_workers`` reuses ``short_rag`` sizing; concurrency
    # is controlled by :class:`ParallelWorkersConfig`.
    "parallel_workers": _TestSpec("parallel_workers", 2000, 500),
}


# Single repeated token-ish chunk. ``"lorem "`` is 6 chars; using the
# 4-chars-per-token heuristic that's ~1.5 tokens per repetition.
_LOREM_CHUNK: str = "lorem "
_CHARS_PER_TOKEN: int = 4  # OpenAI English heuristic


def build_standard_prompt(context_tokens: int) -> str:
    """Build a deterministic prompt sized to ``context_tokens`` tokens.

    Uses the ``≈ 1 token per 4 characters`` heuristic so the output is
    reproducible across runs. The actual provider-tokenized length
    will be within ±10% for English-like text.

    Args:
        context_tokens: Target context length in tokens. Must be ≥ 0.

    Returns:
        A deterministic ASCII string sized to roughly
        ``context_tokens`` tokens.
    """
    if context_tokens <= 0:
        return ""
    target_chars = context_tokens * _CHARS_PER_TOKEN
    # +1 to avoid integer-floor underflow of the desired char count.
    repetitions = (target_chars // len(_LOREM_CHUNK)) + 1
    body = _LOREM_CHUNK * repetitions
    return body[:target_chars]


# ──────────────────────────────────────────────────────────────────────────────
# Resource sampling helpers
# ──────────────────────────────────────────────────────────────────────────────


def _query_nvidia_smi(query: str) -> Optional[list[float]]:
    """Return parsed numeric values from ``nvidia-smi`` for ``query`` or None.

    ``query`` is the value passed to ``--query-gpu=`` (e.g.
    ``memory.used`` or ``utilization.gpu``). Result is a list of
    floats, one per GPU.

    Never raises: missing binary, non-zero exit, or parse failures
    all return ``None``.
    """
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("nvidia-smi %s failed: %s", query, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - never crash the run
        logger.debug("nvidia-smi %s unexpected error: %s", query, exc)
        return None
    if result.returncode != 0:
        return None
    values: list[float] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            values.append(float(line))
        except ValueError:
            continue
    return values or None


class _ResourceSampler:
    """Background sampler for CPU/RAM/GPU/VRAM (1 Hz).

    Samples are collected on a separate task and aggregated on stop:

    * ``ram_peak_gb``: highest RSS observed for the current process.
    * ``cpu_avg_pct``: average ``psutil.Process().cpu_percent`` over
      the run.
    * ``vram_peak_gb``: highest summed VRAM (across GPUs) observed via
      ``nvidia-smi --query-gpu=memory.used``.
    * ``gpu_avg_pct``: average summed utilisation across GPUs.

    All GPU metrics return ``None`` when ``nvidia-smi`` is missing
    or the first sample fails.
    """

    def __init__(self, *, interval_s: float = 1.0) -> None:
        self._interval = max(0.05, float(interval_s))
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

        self._ram_peak_bytes: int = 0
        self._cpu_samples: list[float] = []
        self._vram_peak_mib: float = 0.0
        self._gpu_util_samples: list[float] = []
        self._gpu_available: bool = shutil.which("nvidia-smi") is not None
        self._psutil_process: Any = None
        try:
            import psutil  # type: ignore[import-not-found]
            self._psutil_process = psutil.Process(os.getpid())
            # Prime cpu_percent so the first real sample is meaningful.
            self._psutil_process.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001 - psutil optional
            self._psutil_process = None

    async def __aenter__(self) -> "_ResourceSampler":
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        # Final synchronous sample so very short runs still capture
        # at least one data point.
        self._sample_once()

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._sample_once()
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._interval
                    )
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            return

    def _sample_once(self) -> None:
        # CPU + RAM
        if self._psutil_process is not None:
            try:
                rss = int(self._psutil_process.memory_info().rss)
                if rss > self._ram_peak_bytes:
                    self._ram_peak_bytes = rss
                cpu = float(self._psutil_process.cpu_percent(interval=None))
                self._cpu_samples.append(cpu)
            except Exception:  # noqa: BLE001 - never crash the run
                pass
        # VRAM + GPU util
        if self._gpu_available:
            mem_used = _query_nvidia_smi("memory.used")
            if mem_used is None:
                # nvidia-smi went away mid-run; stop polling it.
                self._gpu_available = False
            else:
                summed = sum(mem_used)
                if summed > self._vram_peak_mib:
                    self._vram_peak_mib = summed
                util = _query_nvidia_smi("utilization.gpu")
                if util:
                    self._gpu_util_samples.append(sum(util))

    @property
    def ram_peak_gb(self) -> Optional[float]:
        if self._ram_peak_bytes <= 0:
            return None
        return round(self._ram_peak_bytes / (1024 ** 3), 4)

    @property
    def cpu_avg_pct(self) -> Optional[float]:
        # Drop the first sample (often 0 on a freshly-created Process).
        meaningful = [s for s in self._cpu_samples if s is not None]
        if len(meaningful) <= 1:
            return None
        return round(sum(meaningful) / len(meaningful), 2)

    @property
    def vram_peak_gb(self) -> Optional[float]:
        if not self._gpu_available and self._vram_peak_mib == 0.0:
            return None
        if self._vram_peak_mib <= 0.0:
            return None
        return round(self._vram_peak_mib / 1024.0, 4)

    @property
    def gpu_avg_pct(self) -> Optional[float]:
        if not self._gpu_util_samples:
            return None
        return round(
            sum(self._gpu_util_samples) / len(self._gpu_util_samples), 2
        )


# ──────────────────────────────────────────────────────────────────────────────
# Provider invocation contract
# ──────────────────────────────────────────────────────────────────────────────

#: Provider callable signature accepted by :class:`RuntimeProfiler`.
#:
#: A profile-runner-friendly callable takes the keyword arguments
#: ``model``, ``provider``, ``prompt``, ``max_tokens`` and returns
#: an awaitable that resolves to a result dict with at least
#: ``output_tokens`` (int). Optional keys: ``first_token_ms``,
#: ``model_load_ms``, ``prompt_tokens_per_second``,
#: ``generation_tokens_per_second``, ``output`` (text).
ProviderInvoker = Callable[..., Awaitable[dict[str, Any]]]


# ──────────────────────────────────────────────────────────────────────────────
# RuntimeProfiler
# ──────────────────────────────────────────────────────────────────────────────


class RuntimeProfiler:
    """Run the standardized prompt suite against a local LLM.

    The profiler is provider-agnostic: it relies on a
    :class:`ModelRoleRegistry`-resolvable ``(provider, model)`` pair
    and a ``provider_invoker`` callable that knows how to issue a
    completion. Tests inject a mock invoker; the worker injects a
    LiteLLM-backed adapter.

    Args:
        model_role_registry: Registry used to validate that the
            target ``(provider, model)`` pair is known. ``None``
            disables validation (the tests inject a mock here).
        profile_store: Persistence backend for measurements.
        host_id: Host identifier recorded on every profile. Defaults
            to :func:`socket.gethostname`.
        tenant: Tenant scope recorded on every profile.
        provider_invoker: Async callable that performs the actual
            provider request. See :data:`ProviderInvoker` for the
            contract. Required at runtime; defaults to ``None`` so
            the class is importable without LLM credentials.
        parallel_workers: Concurrency knobs for the
            ``parallel_workers`` test.
    """

    def __init__(
        self,
        *,
        model_role_registry: Any = None,
        profile_store: RuntimeProfileStore,
        host_id: Optional[str] = None,
        tenant: str = "default",
        provider_invoker: Optional[ProviderInvoker] = None,
        parallel_workers: ParallelWorkersConfig = ParallelWorkersConfig(),
    ) -> None:
        self._registry = model_role_registry
        self._store = profile_store
        self._host_id = host_id or socket.gethostname() or "unknown-host"
        self._tenant = tenant
        self._invoker = provider_invoker
        self._parallel = parallel_workers

    # ── Public API ──────────────────────────────────────────────────

    async def profile_model(
        self,
        *,
        model: str,
        provider: str,
        contexts: Optional[list[int]] = None,
        output_tokens: int = 500,  # noqa: ARG002 - kept for brief compatibility
    ) -> list[RuntimeModelProfile]:
        """Run the full standardized suite for a single model.

        Args:
            model: Model identifier.
            provider: Provider identifier (``ollama``, ``openai``, ...).
            contexts: Optional override list of context sizes; when
                provided, only tests whose ``context_tokens`` value
                is in this list are executed (parallel_workers is
                always included).
            output_tokens: Reserved for brief-API compatibility; the
                output size per test is taken from
                :data:`STANDARD_TEST_SPECS` so the suite stays
                comparable across runs.

        Returns:
            All :class:`RuntimeModelProfile` records emitted by the
            suite, in execution order. Failed runs are present with
            ``success=False``.
        """
        results: list[RuntimeModelProfile] = []
        for test_name in STANDARD_TEST_NAMES:
            spec = STANDARD_TEST_SPECS[test_name]
            if (
                contexts is not None
                and test_name != "parallel_workers"
                and spec.context_tokens not in contexts
            ):
                continue
            try:
                if test_name == "parallel_workers":
                    profile = await self._run_parallel_workers(
                        model=model, provider=provider
                    )
                else:
                    profile = await self.run_test(
                        model=model,
                        provider=provider,
                        test_name=test_name,
                        context_tokens=spec.context_tokens,
                        output_tokens=spec.output_tokens,
                    )
            except Exception as exc:  # noqa: BLE001 - persist as failure
                logger.exception(
                    "Unhandled error in suite test %s for %s/%s",
                    test_name,
                    provider,
                    model,
                )
                profile = await self._save_failure(
                    model=model,
                    provider=provider,
                    test_name=test_name,
                    context_tokens=spec.context_tokens,
                    output_tokens=spec.output_tokens,
                    error=str(exc),
                )
            results.append(profile)
        return results

    async def run_test(
        self,
        *,
        model: str,
        provider: str,
        test_name: str,
        context_tokens: int,
        output_tokens: int,
    ) -> RuntimeModelProfile:
        """Run a single standardized test and persist the result.

        Args:
            model: Model identifier.
            provider: Provider identifier.
            test_name: One of :data:`STANDARD_TEST_NAMES`.
            context_tokens: Target prompt context length in tokens.
            output_tokens: Target generation length in tokens.

        Returns:
            The persisted :class:`RuntimeModelProfile`. Success and
            failure paths both return a saved profile.
        """
        if test_name not in STANDARD_TEST_NAMES:
            raise ValueError(
                f"Unknown test_name: {test_name!r}. "
                f"Expected one of {STANDARD_TEST_NAMES!r}."
            )
        if self._invoker is None:
            return await self._save_failure(
                model=model,
                provider=provider,
                test_name=test_name,
                context_tokens=context_tokens,
                output_tokens=output_tokens,
                error="provider_invoker is not configured",
            )

        prompt = build_standard_prompt(context_tokens)
        sampler = _ResourceSampler()
        start_perf = time.perf_counter()
        provider_result: dict[str, Any] = {}
        error: Optional[str] = None

        try:
            async with sampler:
                provider_result = await self._invoker(
                    model=model,
                    provider=provider,
                    prompt=prompt,
                    max_tokens=output_tokens,
                )
        except Exception as exc:  # noqa: BLE001 - record as failure
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Provider invocation failed for %s/%s test=%s: %s",
                provider,
                model,
                test_name,
                error,
            )

        total_latency_ms = max(0, int((time.perf_counter() - start_perf) * 1000))

        if error is not None:
            profile = self._build_profile(
                model=model,
                provider=provider,
                test_name=test_name,
                context_tokens=context_tokens,
                output_tokens=output_tokens,
                total_latency_ms=total_latency_ms,
                provider_result={},
                sampler=sampler,
                success=False,
                error=error,
                cost_eur=None,
                cost_source=None,
            )
        else:
            actual_output = int(
                provider_result.get("output_tokens", output_tokens) or output_tokens
            )
            cost_eur, cost_source = self._compute_cost(
                model=model,
                provider_result=provider_result,
                prompt_tokens=context_tokens,
                completion_tokens=actual_output,
            )
            profile = self._build_profile(
                model=model,
                provider=provider,
                test_name=test_name,
                context_tokens=context_tokens,
                output_tokens=actual_output,
                total_latency_ms=total_latency_ms,
                provider_result=provider_result,
                sampler=sampler,
                success=True,
                error=None,
                cost_eur=cost_eur,
                cost_source=cost_source,
            )

        return await self._store.save(profile)

    # ── Internal helpers ────────────────────────────────────────────

    async def _run_parallel_workers(
        self, *, model: str, provider: str
    ) -> RuntimeModelProfile:
        """Run N concurrent ``short_rag`` calls and persist a degraded-throughput record."""
        base_spec = STANDARD_TEST_SPECS[self._parallel.base_test]
        n = max(1, int(self._parallel.concurrency))
        prompt = build_standard_prompt(base_spec.context_tokens)

        if self._invoker is None:
            return await self._save_failure(
                model=model,
                provider=provider,
                test_name="parallel_workers",
                context_tokens=base_spec.context_tokens,
                output_tokens=base_spec.output_tokens,
                error="provider_invoker is not configured",
            )

        sampler = _ResourceSampler()
        start_perf = time.perf_counter()
        results: list[Any]
        try:
            async with sampler:
                results = await asyncio.gather(
                    *[
                        self._invoker(
                            model=model,
                            provider=provider,
                            prompt=prompt,
                            max_tokens=base_spec.output_tokens,
                        )
                        for _ in range(n)
                    ],
                    return_exceptions=True,
                )
        except Exception as exc:  # noqa: BLE001 - extreme failure path
            return await self._save_failure(
                model=model,
                provider=provider,
                test_name="parallel_workers",
                context_tokens=base_spec.context_tokens,
                output_tokens=base_spec.output_tokens,
                error=f"{type(exc).__name__}: {exc}",
            )

        total_latency_ms = max(0, int((time.perf_counter() - start_perf) * 1000))

        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, BaseException)]

        if not successes:
            err = "; ".join(
                f"{type(f).__name__}: {f}" for f in failures
            ) or "all parallel workers failed"
            return await self._save_failure(
                model=model,
                provider=provider,
                test_name="parallel_workers",
                context_tokens=base_spec.context_tokens,
                output_tokens=base_spec.output_tokens,
                error=err,
                total_latency_ms=total_latency_ms,
                sampler=sampler,
            )

        agg_output_tokens = sum(
            int(r.get("output_tokens", base_spec.output_tokens) or base_spec.output_tokens)
            for r in successes
        )

        # Per-spec: tokens_per_second for the parallel record is the
        # aggregate divided by N (degradation indicator vs single run).
        if total_latency_ms > 0:
            aggregate_tps = (agg_output_tokens / (total_latency_ms / 1000.0))
            tokens_per_second = aggregate_tps / n
        else:
            tokens_per_second = 0.0

        # Average provider-reported gen tps when available.
        gen_tps_values = [
            float(r.get("generation_tokens_per_second"))
            for r in successes
            if r.get("generation_tokens_per_second") is not None
        ]
        prompt_tps_values = [
            float(r.get("prompt_tokens_per_second"))
            for r in successes
            if r.get("prompt_tokens_per_second") is not None
        ]
        first_tokens = [
            int(r.get("first_token_ms"))
            for r in successes
            if r.get("first_token_ms") is not None
        ]

        # Per-worker cost aggregation: average across workers when every
        # successful worker yields a numeric cost; if any worker has
        # ``None`` (unknown), the aggregate is ``None`` so we don't
        # fabricate a partial number. ``cost_source`` mirrors the worst
        # provenance: ``provider`` only when *all* workers reported
        # provider cost; ``estimated`` when at least one estimate was
        # used; ``unknown`` when nothing could be resolved.
        per_worker_costs: list[tuple[Optional[float], Optional[str]]] = [
            self._compute_cost(
                model=model,
                provider_result=r,
                prompt_tokens=base_spec.context_tokens,
                completion_tokens=int(
                    r.get("output_tokens", base_spec.output_tokens)
                    or base_spec.output_tokens
                ),
            )
            for r in successes
        ]
        if per_worker_costs and all(c is not None for c, _ in per_worker_costs):
            agg_cost: Optional[float] = sum(
                float(c) for c, _ in per_worker_costs if c is not None
            ) / len(per_worker_costs)
            sources = {s for _, s in per_worker_costs}
            if sources == {"provider"}:
                agg_source: Optional[str] = "provider"
            elif "estimated" in sources:
                agg_source = "estimated"
            else:
                agg_source = "unknown"
        else:
            agg_cost = None
            agg_source = "unknown" if per_worker_costs else None

        profile = RuntimeModelProfile(
            id=str(uuid.uuid4()),
            host_id=self._host_id,
            tenant=self._tenant,
            model=model,
            provider=provider,
            quantization=None,
            test_name="parallel_workers",
            context_tokens=base_spec.context_tokens,
            output_tokens=int(agg_output_tokens / max(1, len(successes))),
            model_load_ms=None,
            first_token_ms=(
                int(sum(first_tokens) / len(first_tokens))
                if first_tokens
                else None
            ),
            total_latency_ms=total_latency_ms,
            tokens_per_second=round(float(tokens_per_second), 3),
            prompt_tokens_per_second=(
                round(sum(prompt_tps_values) / len(prompt_tps_values), 3)
                if prompt_tps_values
                else None
            ),
            generation_tokens_per_second=(
                round(sum(gen_tps_values) / len(gen_tps_values), 3)
                if gen_tps_values
                else None
            ),
            ram_peak_gb=sampler.ram_peak_gb,
            vram_peak_gb=sampler.vram_peak_gb,
            cpu_avg_pct=sampler.cpu_avg_pct,
            gpu_avg_pct=sampler.gpu_avg_pct,
            cost_eur=agg_cost,
            cost_source=agg_source,  # type: ignore[arg-type]
            success=True,
            error=(
                f"{len(failures)}/{n} workers failed"
                if failures
                else None
            ),
        )
        return await self._store.save(profile)

    def _build_profile(
        self,
        *,
        model: str,
        provider: str,
        test_name: str,
        context_tokens: int,
        output_tokens: int,
        total_latency_ms: int,
        provider_result: dict[str, Any],
        sampler: _ResourceSampler,
        success: bool,
        error: Optional[str],
        cost_eur: Optional[float] = None,
        cost_source: Optional[str] = None,
    ) -> RuntimeModelProfile:
        """Assemble a :class:`RuntimeModelProfile` from a measured run."""
        if success and total_latency_ms > 0 and output_tokens > 0:
            tokens_per_second = output_tokens / (total_latency_ms / 1000.0)
        else:
            tokens_per_second = 0.0

        first_token_ms = provider_result.get("first_token_ms")
        if first_token_ms is not None:
            first_token_ms = int(first_token_ms)

        model_load_ms = provider_result.get("model_load_ms")
        if model_load_ms is not None:
            model_load_ms = int(model_load_ms)

        return RuntimeModelProfile(
            id=str(uuid.uuid4()),
            host_id=self._host_id,
            tenant=self._tenant,
            model=model,
            provider=provider,
            quantization=provider_result.get("quantization"),
            test_name=test_name,  # type: ignore[arg-type]
            context_tokens=int(context_tokens),
            output_tokens=int(output_tokens),
            model_load_ms=model_load_ms,
            first_token_ms=first_token_ms,
            total_latency_ms=int(total_latency_ms),
            tokens_per_second=round(float(tokens_per_second), 3),
            prompt_tokens_per_second=(
                float(provider_result["prompt_tokens_per_second"])
                if provider_result.get("prompt_tokens_per_second") is not None
                else None
            ),
            generation_tokens_per_second=(
                float(provider_result["generation_tokens_per_second"])
                if provider_result.get("generation_tokens_per_second") is not None
                else None
            ),
            ram_peak_gb=sampler.ram_peak_gb,
            vram_peak_gb=sampler.vram_peak_gb,
            cpu_avg_pct=sampler.cpu_avg_pct,
            gpu_avg_pct=sampler.gpu_avg_pct,
            cost_eur=cost_eur,
            cost_source=cost_source,  # type: ignore[arg-type]
            success=success,
            error=error,
        )

    # ── Cost helpers (F4 / Task 82) ────────────────────────────────────

    def _compute_cost(
        self,
        *,
        model: str,
        provider_result: dict[str, Any],
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
    ) -> tuple[Optional[float], Optional[str]]:
        """Resolve ``(cost_eur, cost_source)`` for a single provider run.

        Resolution order:

        1. Explicit provider-reported cost via :func:`_extract_provider_cost`
           (LiteLLM/OpenAI-compat keys).
        2. Estimate from :class:`ModelRoleConfig` cost rates resolved via
           :meth:`_lookup_role_config_for_model`.
        3. ``(None, "unknown")`` when neither is available (typical for
           local providers like Ollama with no role rates configured).

        Args:
            model: Model identifier used to look up role config rates.
            provider_result: Raw provider response dict.
            prompt_tokens: Best-known prompt token count for the run.
            completion_tokens: Best-known generated token count.

        Returns:
            Tuple ``(cost_eur, cost_source)``. ``cost_source`` is one of
            ``"provider"``, ``"estimated"``, ``"unknown"``.
        """
        provider_cost = _extract_provider_cost(provider_result)
        if provider_cost is not None:
            return float(provider_cost), "provider"
        role_config = self._lookup_role_config_for_model(model)
        return self._estimate_cost_eur(
            role_config=role_config,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _estimate_cost_eur(
        self,
        *,
        role_config: Any,
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
    ) -> tuple[Optional[float], str]:
        """Estimate cost in EUR from per-million token rates.

        Reads ``cost_per_million_input`` and ``cost_per_million_output``
        from the supplied :class:`ModelRoleConfig`. Missing rates are
        treated as ``0.0``; when *both* rates are missing, the helper
        returns ``(None, "unknown")`` so callers can distinguish a
        true zero-cost run (e.g. a free preview tier) from "no rate
        information".

        Args:
            role_config: ``ModelRoleConfig`` instance or ``None``.
            prompt_tokens: Prompt token count; ``None`` is treated as 0.
            completion_tokens: Generated token count; ``None`` is
                treated as 0.

        Returns:
            Tuple ``(cost_eur, source)`` where ``source`` is
            ``"estimated"`` when at least one rate was available, else
            ``"unknown"``.
        """
        if role_config is None:
            return None, "unknown"
        rate_in = getattr(role_config, "cost_per_million_input", None)
        rate_out = getattr(role_config, "cost_per_million_output", None)
        if rate_in is None and rate_out is None:
            return None, "unknown"
        try:
            p = int(prompt_tokens) if prompt_tokens is not None else 0
            c = int(completion_tokens) if completion_tokens is not None else 0
            r_in = float(rate_in) if rate_in is not None else 0.0
            r_out = float(rate_out) if rate_out is not None else 0.0
        except (TypeError, ValueError):
            return None, "unknown"
        cost = (p / 1_000_000.0) * r_in + (c / 1_000_000.0) * r_out
        return float(cost), "estimated"

    def _lookup_role_config_for_model(self, model: str) -> Any:
        """Return the first registered ``ModelRoleConfig`` matching ``model``.

        The registry is keyed by ``role_id`` (orchestrator, worker, ...)
        so we scan ``list_roles()`` and match on the ``model`` attribute.
        Returns ``None`` when no registry is configured, the model is
        not registered, or the registry lookup fails.
        """
        registry = self._registry
        if registry is None:
            return None
        try:
            list_roles = getattr(registry, "list_roles", None)
            roles: dict[str, Any] = list_roles() if callable(list_roles) else {}
            for cfg in roles.values():
                if getattr(cfg, "model", None) == model:
                    return cfg
        except Exception:  # noqa: BLE001 - registry lookup never crashes profiling
            return None
        return None

    async def _save_failure(
        self,
        *,
        model: str,
        provider: str,
        test_name: str,
        context_tokens: int,
        output_tokens: int,
        error: str,
        total_latency_ms: int = 0,
        sampler: Optional[_ResourceSampler] = None,
    ) -> RuntimeModelProfile:
        """Persist a ``success=False`` profile and return it."""
        profile = RuntimeModelProfile(
            id=str(uuid.uuid4()),
            host_id=self._host_id,
            tenant=self._tenant,
            model=model,
            provider=provider,
            quantization=None,
            test_name=test_name,  # type: ignore[arg-type]
            context_tokens=int(context_tokens),
            output_tokens=int(output_tokens),
            model_load_ms=None,
            first_token_ms=None,
            total_latency_ms=int(total_latency_ms),
            tokens_per_second=0.0,
            prompt_tokens_per_second=None,
            generation_tokens_per_second=None,
            ram_peak_gb=sampler.ram_peak_gb if sampler is not None else None,
            vram_peak_gb=sampler.vram_peak_gb if sampler is not None else None,
            cpu_avg_pct=sampler.cpu_avg_pct if sampler is not None else None,
            gpu_avg_pct=sampler.gpu_avg_pct if sampler is not None else None,
            success=False,
            error=error,
        )
        return await self._store.save(profile)
