"""Frozen catalog of LLM/embedding models the profiler knows about.

This is intentionally decoupled from ``backend/core/config.py`` so the tool
works on machines that have no Quellex install. Keep this list small and
curated - it is the canonical demo-facing inventory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogModel:
    model_id: str            # exact id, e.g. "qwen2.5:14b", "gpt-4o"
    display_name: str
    provider: str            # "ollama", "openai", "google", "anthropic", "local-embed"
    runs_locally: bool
    context_window: int
    vram_required_gb: float  # minimum for Q4_K_M quant if local; 0 for API
    strengths: tuple[str, ...] = ()


# ---- Orchestrator-grade (reasoning/planning) ----
ORCHESTRATORS: dict[str, CatalogModel] = {
    "gpt-4o": CatalogModel(
        "gpt-4o", "GPT-4o", "openai", False, 128_000, 0.0,
        ("strong reasoning", "tool use", "multilingual"),
    ),
    "gemini-2.5-pro": CatalogModel(
        "gemini-2.5-pro", "Gemini 2.5 Pro", "google", False, 1_000_000, 0.0,
        ("huge context", "multimodal"),
    ),
    "claude-3-5-sonnet": CatalogModel(
        "claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "anthropic", False,
        200_000, 0.0, ("instruction following", "writing quality"),
    ),
    "qwen2.5:14b": CatalogModel(
        "qwen2.5:14b", "Qwen 2.5 14B (local)", "ollama", True, 32_768, 10.0,
        ("reasoning", "multilingual", "runs on 16GB VRAM"),
    ),
    "llama3.1:8b": CatalogModel(
        "llama3.1:8b", "Llama 3.1 8B (local)", "ollama", True, 128_000, 6.0,
        ("tool use", "128k context"),
    ),
    "qwen2.5:32b-instruct": CatalogModel(
        "qwen2.5:32b-instruct", "Qwen 2.5 32B Instruct (local)", "ollama", True,
        32_768, 22.0, ("near GPT-4-class reasoning", "fully local"),
    ),
    "qwen2.5:72b": CatalogModel(
        "qwen2.5:72b", "Qwen 2.5 72B (local)", "ollama", True, 32_768, 48.0,
        ("top-tier local reasoning",),
    ),
    "llama3.3:70b": CatalogModel(
        "llama3.3:70b", "Llama 3.3 70B (local)", "ollama", True, 128_000, 48.0,
        ("128k context", "top-tier local"),
    ),
}


# ---- Worker-grade (fast execution) ----
WORKERS: dict[str, CatalogModel] = {
    "gemini-2.0-flash": CatalogModel(
        "gemini-2.0-flash", "Gemini 2.0 Flash", "google", False, 1_000_000, 0.0,
        ("very fast", "cheap", "huge context"),
    ),
    "gpt-4o-mini": CatalogModel(
        "gpt-4o-mini", "GPT-4o mini", "openai", False, 128_000, 0.0,
        ("fast", "reliable tool use"),
    ),
    "llama3.2:3b": CatalogModel(
        "llama3.2:3b", "Llama 3.2 3B (local)", "ollama", True, 128_000, 3.0,
        ("runs on iGPU/CPU", "128k context"),
    ),
    "qwen2.5:7b": CatalogModel(
        "qwen2.5:7b", "Qwen 2.5 7B (local)", "ollama", True, 32_768, 5.0,
        ("balanced local worker",),
    ),
    "qwen2.5:14b": CatalogModel(
        "qwen2.5:14b", "Qwen 2.5 14B (local)", "ollama", True, 32_768, 10.0,
        ("high-quality local worker",),
    ),
    "llama3.1:8b": CatalogModel(
        "llama3.1:8b", "Llama 3.1 8B (local)", "ollama", True, 128_000, 6.0,
        ("128k context worker", "tool use"),
    ),
}


# ---- Embedding models ----
EMBEDDINGS: dict[str, CatalogModel] = {
    "text-embedding-3-small": CatalogModel(
        "text-embedding-3-small", "OpenAI Embedding v3 (small)", "openai",
        False, 8_191, 0.0, ("1536 dims", "very cheap"),
    ),
    "bge-small-en-v1.5": CatalogModel(
        "bge-small-en-v1.5", "BGE Small EN v1.5 (local)", "local-embed",
        True, 512, 0.5, ("384 dims", "CPU friendly"),
    ),
    "bge-m3": CatalogModel(
        "bge-m3", "BGE-M3 multilingual (local)", "local-embed",
        True, 8_192, 2.0, ("1024 dims", "multilingual", "hybrid sparse+dense"),
    ),
}


# Rough static tokens/sec lookup table for the UI bar chart. Values are
# order-of-magnitude honest but not guaranteed - they reflect typical
# Q4_K_M quant throughput on the listed class of hardware. Cloud rows are
# wall-clock end-to-end estimates from European regions.
TOKENS_PER_SEC_TABLE: dict[tuple[str, str], float] = {
    # (model_id, hw_bucket) -> tok/s
    # hw_bucket: "cloud", "rtx-4090", "rtx-3090", "rtx-4070", "rtx-3060",
    # "apple-max", "apple-pro", "apple-base", "cpu-only"
    ("gpt-4o", "cloud"): 80.0,
    ("gpt-4o-mini", "cloud"): 150.0,
    ("gemini-2.5-pro", "cloud"): 60.0,
    ("gemini-2.0-flash", "cloud"): 180.0,
    ("claude-3-5-sonnet-20241022", "cloud"): 70.0,

    ("qwen2.5:14b", "rtx-4090"): 55.0,
    ("qwen2.5:14b", "rtx-3090"): 45.0,
    ("qwen2.5:14b", "rtx-4070"): 30.0,
    ("qwen2.5:14b", "rtx-3060"): 15.0,
    ("qwen2.5:14b", "apple-max"): 28.0,
    ("qwen2.5:14b", "apple-pro"): 18.0,
    ("qwen2.5:14b", "cpu-only"): 4.0,

    ("llama3.1:8b", "rtx-4090"): 85.0,
    ("llama3.1:8b", "rtx-3090"): 70.0,
    ("llama3.1:8b", "rtx-4070"): 55.0,
    ("llama3.1:8b", "rtx-3060"): 30.0,
    ("llama3.1:8b", "apple-max"): 45.0,
    ("llama3.1:8b", "apple-pro"): 32.0,
    ("llama3.1:8b", "apple-base"): 18.0,
    ("llama3.1:8b", "cpu-only"): 6.0,

    ("qwen2.5:32b-instruct", "rtx-4090"): 28.0,
    ("qwen2.5:32b-instruct", "rtx-3090"): 22.0,
    ("qwen2.5:32b-instruct", "apple-max"): 16.0,

    ("qwen2.5:72b", "rtx-4090"): 12.0,
    ("qwen2.5:72b", "apple-max"): 8.0,
    ("llama3.3:70b", "rtx-4090"): 11.0,
    ("llama3.3:70b", "apple-max"): 8.0,

    ("llama3.2:3b", "rtx-4090"): 200.0,
    ("llama3.2:3b", "rtx-3060"): 95.0,
    ("llama3.2:3b", "apple-pro"): 80.0,
    ("llama3.2:3b", "apple-base"): 55.0,
    ("llama3.2:3b", "cpu-only"): 20.0,

    ("qwen2.5:7b", "rtx-4090"): 95.0,
    ("qwen2.5:7b", "rtx-3060"): 40.0,
    ("qwen2.5:7b", "apple-pro"): 38.0,
    ("qwen2.5:7b", "cpu-only"): 8.0,
}


def tokens_per_sec(model_id: str, hw_bucket: str) -> float:
    """Return tok/s lookup or a safe fallback (0 if unknown)."""
    return TOKENS_PER_SEC_TABLE.get((model_id, hw_bucket), 0.0)
