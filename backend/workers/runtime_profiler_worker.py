"""Runtime profiler worker (Phase 6 / Task 70).

Standalone worker that runs the standardized prompt suite from
:class:`backend.services.runtime_profiler.RuntimeProfiler` against
each requested model serially (parallel model loads OOM the host)
and persists results into ``runtime_model_profiles`` via
:class:`MongoRuntimeProfileStore`.

Usage::

    python -m backend.workers.runtime_profiler_worker \\
        --models gemma3:4b,gemma3:26b \\
        --provider ollama

Importable without side effects: no DB connection or provider
initialisation happens at module load. Wire-up only happens inside
:func:`main` and helpers it calls.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
from typing import Any, Optional

from backend.agent.strategy.runtime_profile_store import (
    MongoRuntimeProfileStore,
    RuntimeProfileStore,
)
from backend.services.runtime_profiler import RuntimeProfiler

logger = logging.getLogger(__name__)

__all__ = [
    "build_arg_parser",
    "build_litellm_invoker",
    "main",
    "run_profiler",
]


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    """Return the worker's argparse parser (importable for tests)."""
    parser = argparse.ArgumentParser(
        prog="python -m backend.workers.runtime_profiler_worker",
        description=(
            "Run the standardized LLM benchmark suite against one or "
            "more models and persist results into runtime_model_profiles."
        ),
    )
    parser.add_argument(
        "--models",
        type=str,
        required=True,
        help=(
            "Comma-separated list of model identifiers, e.g. "
            "'gemma3:4b,gemma3:26b'."
        ),
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="ollama",
        help="Provider identifier for all models (default: ollama).",
    )
    parser.add_argument(
        "--tenant",
        type=str,
        default=os.getenv("RUNTIME_PROFILER_TENANT", "default"),
        help="Tenant scope tag stored on every profile (default: 'default').",
    )
    parser.add_argument(
        "--host-id",
        type=str,
        default=os.getenv("RUNTIME_PROFILER_HOST_ID") or socket.gethostname(),
        help="Host identifier (default: hostname).",
    )
    parser.add_argument(
        "--mongodb-uri",
        type=str,
        default=os.getenv(
            "MONGODB_URI", "mongodb://localhost:27017/?directConnection=true"
        ),
        help="MongoDB connection string (default: $MONGODB_URI).",
    )
    parser.add_argument(
        "--mongodb-database",
        type=str,
        default=os.getenv("MONGODB_DATABASE", "rag_db"),
        help="Database name (default: $MONGODB_DATABASE or rag_db).",
    )
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help=(
            "Use the InMemoryRuntimeProfileStore instead of Mongo. "
            "Useful for smoke-testing without a database."
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Python logging level (default: INFO).",
    )
    return parser


# ──────────────────────────────────────────────────────────────────────────────
# Provider invoker (LiteLLM-backed)
# ──────────────────────────────────────────────────────────────────────────────


def build_litellm_invoker() -> Any:
    """Return an async LiteLLM-backed provider invoker.

    The invoker matches the signature documented on
    :data:`backend.services.runtime_profiler.ProviderInvoker`. It is
    constructed lazily so the worker module remains importable on
    hosts without LiteLLM installed.
    """
    from litellm import acompletion  # local import — keeps module import-safe

    async def _invoke(
        *,
        model: str,
        provider: str,
        prompt: str,
        max_tokens: int,
        **_: Any,
    ) -> dict[str, Any]:
        # Re-encode model id in LiteLLM's prefixed form for non-OpenAI
        # providers. Callers that already supply the prefix get a
        # no-op.
        litellm_model = model
        prefix = f"{provider}/"
        if provider not in {"openai"} and not litellm_model.startswith(prefix):
            litellm_model = f"{prefix}{litellm_model}"

        response = await acompletion(
            model=litellm_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )

        usage = getattr(response, "usage", None)
        completion_tokens: Optional[int] = None
        if usage is not None:
            completion_tokens = (
                getattr(usage, "completion_tokens", None)
                or (usage.get("completion_tokens") if isinstance(usage, dict) else None)
            )
        return {
            "output_tokens": int(completion_tokens or max_tokens),
            "first_token_ms": None,
            "model_load_ms": None,
            "prompt_tokens_per_second": None,
            "generation_tokens_per_second": None,
        }

    return _invoke


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────


async def run_profiler(
    *,
    models: list[str],
    provider: str,
    tenant: str,
    host_id: str,
    profile_store: RuntimeProfileStore,
    invoker: Optional[Any] = None,
) -> dict[str, int]:
    """Run the suite for each model serially and return a count summary.

    Args:
        models: List of model identifiers to profile.
        provider: Provider identifier shared across all models.
        tenant: Tenant scope tag stored on every profile.
        host_id: Host identifier stored on every profile.
        profile_store: Concrete persistence backend.
        invoker: Optional provider invoker; defaults to
            :func:`build_litellm_invoker`.

    Returns:
        ``{"models": int, "profiles": int, "failures": int}`` summary.
    """
    if invoker is None:
        invoker = build_litellm_invoker()
    await profile_store.ensure_indexes()

    profiler = RuntimeProfiler(
        model_role_registry=None,
        profile_store=profile_store,
        host_id=host_id,
        tenant=tenant,
        provider_invoker=invoker,
    )

    total_profiles = 0
    total_failures = 0
    for idx, model in enumerate(models, start=1):
        logger.info(
            "[%d/%d] Profiling %s/%s ...", idx, len(models), provider, model
        )
        results = await profiler.profile_model(
            model=model, provider=provider
        )
        total_profiles += len(results)
        total_failures += sum(1 for r in results if not r.success)
        for r in results:
            logger.info(
                "  test=%s success=%s tps=%.2f total_ms=%d ram_gb=%s vram_gb=%s",
                r.test_name,
                r.success,
                r.tokens_per_second,
                r.total_latency_ms,
                r.ram_peak_gb,
                r.vram_peak_gb,
            )

    summary = {
        "models": len(models),
        "profiles": total_profiles,
        "failures": total_failures,
    }
    logger.info("Runtime profiler summary: %s", summary)
    return summary


async def main(argv: Optional[list[str]] = None) -> int:
    """Worker entrypoint.

    Args:
        argv: Optional CLI argument override (used by tests). When
            ``None``, ``sys.argv[1:]`` is parsed.

    Returns:
        Process exit code (0 on success).
    """
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        logger.error("No models supplied via --models; aborting.")
        return 2

    if args.in_memory:
        from backend.agent.strategy.runtime_profile_store import (
            InMemoryRuntimeProfileStore,
        )

        store: RuntimeProfileStore = InMemoryRuntimeProfileStore()
    else:
        # Local import: keeps the module importable on hosts without
        # PyMongo Async wired up in the global namespace.
        from pymongo import AsyncMongoClient

        client = AsyncMongoClient(
            args.mongodb_uri, serverSelectionTimeoutMS=10000
        )
        db = client[args.mongodb_database]
        store = MongoRuntimeProfileStore(db)

    try:
        await run_profiler(
            models=models,
            provider=args.provider,
            tenant=args.tenant,
            host_id=args.host_id,
            profile_store=store,
        )
    except Exception:  # noqa: BLE001 - surface as non-zero exit
        logger.exception("Runtime profiler worker failed")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(asyncio.run(main()))
