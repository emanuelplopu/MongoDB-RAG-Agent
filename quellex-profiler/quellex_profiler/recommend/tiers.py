"""Tier classification rules from a :class:`SystemProfile`."""

from __future__ import annotations

from dataclasses import dataclass

from quellex_profiler.schema import SystemProfile


@dataclass(frozen=True)
class TierSpec:
    key: str
    label: str
    summary: str


TIER_CLOUD = TierSpec(
    "cloud-only",
    "Cloud-Only",
    "The machine does not have enough memory or GPU capacity for local "
    "reasoning models. Quellex runs fully through managed APIs; embeddings "
    "and retrieval happen in MongoDB Atlas.",
)
TIER_HYBRID = TierSpec(
    "hybrid-small",
    "Hybrid (Cloud Orchestrator + Local Worker)",
    "A fast cloud model coordinates reasoning while a small local model "
    "handles high-volume worker tasks. Embeddings run locally for privacy.",
)
TIER_LOCAL_BALANCED = TierSpec(
    "local-balanced",
    "Local Balanced",
    "Quellex runs fully on-premise with a 7-14B orchestrator and a 3B "
    "worker. Suitable for typical Kanzlei workloads with confidential data.",
)
TIER_LOCAL_PREMIUM = TierSpec(
    "local-premium",
    "Local Premium",
    "Enough VRAM for a 30B+ orchestrator. Fully local operation with "
    "high answer quality and zero data leaving the firm.",
)
TIER_WORKSTATION = TierSpec(
    "workstation",
    "Workstation / Enterprise",
    "Workstation-class hardware. 70B+ local orchestrator with a mid-size "
    "local worker and fully local embeddings - maximum capability, maximum "
    "privacy.",
)


def classify_tier(profile: SystemProfile) -> TierSpec:
    """Classify the system into a Quellex capability tier.

    Order matters: we test from largest to smallest and return the first match.
    Uses ``total_accel_vram_gb`` which already encodes Apple unified memory.
    """
    ram = profile.ram_gb
    vram = profile.total_accel_vram_gb
    cc = _max_compute_capability(profile)

    # Older NVIDIA cards (compute cap < 7.0) get downgraded one tier.
    downgrade = (cc is not None and cc < 7.0)

    if ram >= 128 and vram >= 48 and not downgrade:
        return TIER_WORKSTATION
    if ram >= 64 and vram >= 22 and not downgrade:
        return TIER_LOCAL_PREMIUM
    if ram >= 32 and vram >= 10:
        # Downgrade path from premium falls in here.
        return TIER_LOCAL_BALANCED
    if ram >= 16 and (vram >= 4 or profile.is_apple_silicon):
        return TIER_HYBRID
    return TIER_CLOUD


def _max_compute_capability(profile: SystemProfile) -> float | None:
    values: list[float] = []
    for g in profile.gpus:
        if g.vendor == "nvidia" and g.compute_capability:
            try:
                values.append(float(g.compute_capability))
            except ValueError:
                continue
    return max(values) if values else None
