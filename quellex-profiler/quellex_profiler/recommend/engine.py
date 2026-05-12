"""Turn a :class:`SystemProfile` into a full :class:`Recommendation`."""

from __future__ import annotations

from quellex_profiler.schema import ModelChoice, Recommendation, SystemProfile
from quellex_profiler.recommend.models import (
    CatalogModel,
    EMBEDDINGS,
    ORCHESTRATORS,
    WORKERS,
    tokens_per_sec,
)
from quellex_profiler.recommend.tiers import (
    TIER_CLOUD,
    TIER_HYBRID,
    TIER_LOCAL_BALANCED,
    TIER_LOCAL_PREMIUM,
    TIER_WORKSTATION,
    classify_tier,
)


def _hw_bucket(profile: SystemProfile) -> str:
    """Return a short hardware-class key used by the tok/s lookup table."""
    if profile.is_apple_silicon:
        # Approximate Apple tiers by unified memory.
        if profile.ram_gb >= 64:
            return "apple-max"
        if profile.ram_gb >= 32:
            return "apple-pro"
        return "apple-base"

    # NVIDIA buckets by VRAM (Q4_K_M realistic throughput).
    for g in profile.gpus:
        if g.vendor != "nvidia":
            continue
        name = g.model.lower()
        if "4090" in name or g.vram_gb >= 24:
            return "rtx-4090"
        if "3090" in name:
            return "rtx-3090"
        if "4080" in name or "4070" in name or (12 <= g.vram_gb < 20):
            return "rtx-4070"
        if "3060" in name or (6 <= g.vram_gb < 12):
            return "rtx-3060"
    return "cpu-only"


def _choose(
    role: str,
    cat: CatalogModel,
    rationale: str,
    alternatives: list[str] | None = None,
) -> ModelChoice:
    return ModelChoice(
        role=role,  # type: ignore[arg-type]
        model_id=cat.model_id,
        display_name=cat.display_name,
        provider=cat.provider,
        runs_locally=cat.runs_locally,
        context_window=cat.context_window,
        vram_required_gb=cat.vram_required_gb,
        rationale=rationale,
        alternatives=alternatives or [],
    )


def recommend_config(profile: SystemProfile) -> Recommendation:
    tier = classify_tier(profile)
    caveats: list[str] = []

    # Translate tier -> concrete model picks.
    if tier is TIER_CLOUD:
        orchestrator = _choose(
            "orchestrator",
            ORCHESTRATORS["gpt-4o"],
            "CPU-bound or low-RAM system: the orchestrator runs on a managed API.",
            alternatives=["gemini-2.5-pro", "claude-3-5-sonnet-20241022"],
        )
        worker = _choose(
            "worker",
            WORKERS["gemini-2.0-flash"],
            "Cheapest high-throughput worker with huge context, ideal for RAG fan-out.",
            alternatives=["gpt-4o-mini"],
        )
        embedding = _choose(
            "embedding",
            EMBEDDINGS["text-embedding-3-small"],
            "1536-d OpenAI embeddings; matches Quellex default vector index dims.",
        )
        caveats.append(
            "Sensitive documents leave the network. Add a dedicated VPC egress or "
            "upgrade RAM/GPU to enable a local tier."
        )
    elif tier is TIER_HYBRID:
        orchestrator = _choose(
            "orchestrator",
            ORCHESTRATORS["gemini-2.5-pro"],
            "Orchestrator kept in the cloud (Gemini 2.5 Pro) to preserve RAM/GPU "
            "for the local worker; 1M-token context helps multi-document reasoning.",
            alternatives=["gpt-4o"],
        )
        worker = _choose(
            "worker",
            WORKERS["llama3.2:3b"],
            "Llama 3.2 3B fits on iGPU / 4-8 GB VRAM; handles parallel RAG workers locally.",
            alternatives=["gemini-2.0-flash"],
        )
        embedding = _choose(
            "embedding",
            EMBEDDINGS["bge-small-en-v1.5"],
            "Local CPU-friendly 384-d embeddings keep document vectors inside the firm.",
            alternatives=["bge-m3"],
        )
    elif tier is TIER_LOCAL_BALANCED:
        if profile.is_apple_silicon:
            orch_id = "qwen2.5:14b"
            alts = ["llama3.1:8b"]
        else:
            # Prefer Qwen 14B when enough VRAM, else Llama 3.1 8B.
            if profile.total_accel_vram_gb >= 12:
                orch_id, alts = "qwen2.5:14b", ["llama3.1:8b"]
            else:
                orch_id, alts = "llama3.1:8b", ["qwen2.5:14b"]
        orchestrator = _choose(
            "orchestrator",
            ORCHESTRATORS[orch_id],
            "Fits within available VRAM (Q4_K_M quant) with headroom for context.",
            alternatives=alts,
        )
        worker = _choose(
            "worker",
            WORKERS["llama3.2:3b"],
            "Small fast worker for RAG fan-out; leaves orchestrator VRAM free.",
            alternatives=["qwen2.5:7b"],
        )
        embedding = _choose(
            "embedding",
            EMBEDDINGS["bge-m3"],
            "Multilingual 1024-d hybrid embeddings run fully local.",
        )
    elif tier is TIER_LOCAL_PREMIUM:
        orchestrator = _choose(
            "orchestrator",
            ORCHESTRATORS["qwen2.5:32b-instruct"],
            "24GB+ VRAM fits a 32B Q4_K_M orchestrator with reasoning near GPT-4 class.",
            alternatives=["llama3.3:70b"],
        )
        worker = _choose(
            "worker",
            WORKERS["llama3.1:8b"],
            "8B worker with 128k context for large-document worker calls.",
            alternatives=["qwen2.5:7b"],
        )
        embedding = _choose(
            "embedding",
            EMBEDDINGS["bge-m3"],
            "Multilingual hybrid embeddings; 100% local pipeline.",
        )
    else:  # TIER_WORKSTATION
        orchestrator = _choose(
            "orchestrator",
            ORCHESTRATORS["llama3.3:70b"],
            "Workstation VRAM supports a 70B local orchestrator with 128k context.",
            alternatives=["qwen2.5:72b"],
        )
        worker = _choose(
            "worker",
            WORKERS["qwen2.5:14b"],
            "14B worker gives high-quality parallel answers without using the "
            "orchestrator's VRAM slot.",
            alternatives=["llama3.1:8b"],
        )
        embedding = _choose(
            "embedding",
            EMBEDDINGS["bge-m3"],
            "Top-quality multilingual embeddings, fully local.",
        )

    # Caveats for special cases.
    if profile.arch == "arm64" and not profile.is_apple_silicon:
        caveats.append(
            "Non-Apple ARM64 host: local models will run via llama.cpp on CPU; "
            "expect lower throughput than x86_64 with a dGPU."
        )
    if any(g.vendor == "amd" and not g.is_integrated for g in profile.gpus):
        caveats.append(
            "AMD discrete GPU detected. Ollama supports ROCm on Linux; on Windows "
            "Ollama falls back to CPU for Radeon - plan for lower tok/s or run under WSL."
        )
    if any(g.vendor == "intel" and not g.is_integrated for g in profile.gpus):
        caveats.append(
            "Intel Arc dGPU detected. Local inference via Ollama is experimental; "
            "prefer the cloud orchestrator path for production."
        )

    # Performance + privacy + cost.
    bucket = _hw_bucket(profile)
    tps: dict[str, float] = {}
    for m in (orchestrator, worker, embedding):
        if m.runs_locally:
            tps[m.model_id] = tokens_per_sec(m.model_id, bucket) or tokens_per_sec(
                m.model_id, "cpu-only"
            )
        else:
            tps[m.model_id] = tokens_per_sec(m.model_id, "cloud")

    privacy = _privacy_score(orchestrator, worker, embedding)
    cost_low, cost_high = _cost_estimate(orchestrator, worker, embedding)

    return Recommendation(
        tier=tier.key,
        tier_label=tier.label,
        tier_summary=tier.summary,
        orchestrator=orchestrator,
        workers=[worker],
        embedding=embedding,
        estimated_tokens_per_sec=tps,
        privacy_score=privacy,
        monthly_cost_estimate_eur=(cost_low, cost_high),
        caveats=caveats,
    )


def _privacy_score(
    orchestrator: ModelChoice, worker: ModelChoice, embedding: ModelChoice
) -> int:
    score = 0
    # Embedding locality matters most: if embeddings stay local, documents
    # never leave the firm even when reasoning is cloud.
    if embedding.runs_locally:
        score += 50
    if worker.runs_locally:
        score += 25
    if orchestrator.runs_locally:
        score += 25
    return score


def _cost_estimate(
    orchestrator: ModelChoice, worker: ModelChoice, embedding: ModelChoice
) -> tuple[float, float]:
    """Rough EUR/month estimate for a 10-user Kanzlei (200k queries/mo).

    These are demo-grade numbers, explicitly labelled as estimates in the UI.
    """
    cost = 0.0
    # Orchestrator: ~1k tokens in + 400 out per query.
    if not orchestrator.runs_locally:
        if orchestrator.model_id == "gpt-4o":
            cost += 200_000 * (1.0 * 2.5 + 0.4 * 10.0) / 1_000_000
        elif orchestrator.model_id == "gemini-2.5-pro":
            cost += 200_000 * (1.0 * 1.25 + 0.4 * 5.0) / 1_000_000
        elif "claude" in orchestrator.model_id:
            cost += 200_000 * (1.0 * 3.0 + 0.4 * 15.0) / 1_000_000
    # Worker: ~4k in + 200 out per query, often fan-out 3x.
    if not worker.runs_locally:
        if "gemini" in worker.model_id:
            cost += 3 * 200_000 * (4.0 * 0.075 + 0.2 * 0.3) / 1_000_000
        elif "gpt-4o-mini" in worker.model_id:
            cost += 3 * 200_000 * (4.0 * 0.15 + 0.2 * 0.6) / 1_000_000
    # Embedding: flat cheap.
    if not embedding.runs_locally:
        cost += 20.0  # indicative

    # EUR ~= USD for rough demo; widen to a plausible range.
    low = round(max(0.0, cost * 0.9), 0)
    high = round(cost * 1.25 + (50 if cost > 0 else 0), 0)
    return (low, high)
