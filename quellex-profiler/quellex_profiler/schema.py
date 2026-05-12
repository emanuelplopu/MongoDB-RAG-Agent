"""Typed data contracts for profile, recommendation, and report output."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal


GpuVendor = Literal["nvidia", "amd", "intel", "apple", "unknown"]


@dataclass
class GpuInfo:
    """Describes a single GPU/accelerator detected on the system."""

    vendor: GpuVendor
    model: str
    vram_gb: float
    is_integrated: bool = False
    unified_memory: bool = False
    compute_capability: str | None = None
    driver_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SystemProfile:
    """Aggregated snapshot of the host machine relevant to LLM workload sizing."""

    os_name: str
    os_version: str
    arch: str
    cpu_brand: str
    cpu_vendor: str
    cpu_cores_physical: int
    cpu_cores_logical: int
    cpu_max_ghz: float
    cpu_flags: list[str] = field(default_factory=list)
    ram_gb: float = 0.0
    gpus: list[GpuInfo] = field(default_factory=list)
    total_accel_vram_gb: float = 0.0
    is_apple_silicon: bool = False
    detection_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "os_name": self.os_name,
            "os_version": self.os_version,
            "arch": self.arch,
            "cpu_brand": self.cpu_brand,
            "cpu_vendor": self.cpu_vendor,
            "cpu_cores_physical": self.cpu_cores_physical,
            "cpu_cores_logical": self.cpu_cores_logical,
            "cpu_max_ghz": self.cpu_max_ghz,
            "cpu_flags": self.cpu_flags,
            "ram_gb": self.ram_gb,
            "gpus": [g.to_dict() for g in self.gpus],
            "total_accel_vram_gb": self.total_accel_vram_gb,
            "is_apple_silicon": self.is_apple_silicon,
            "detection_notes": self.detection_notes,
        }


@dataclass
class ModelChoice:
    """A concrete model picked for a role (orchestrator/worker/embedding)."""

    role: Literal["orchestrator", "worker", "embedding"]
    model_id: str
    display_name: str
    provider: str                       # e.g. "ollama", "openai", "google", "anthropic"
    runs_locally: bool
    context_window: int
    vram_required_gb: float = 0.0
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Recommendation:
    """Full configuration recommendation produced from a SystemProfile."""

    tier: str
    tier_label: str
    tier_summary: str
    orchestrator: ModelChoice
    workers: list[ModelChoice]
    embedding: ModelChoice
    estimated_tokens_per_sec: dict[str, float] = field(default_factory=dict)
    privacy_score: int = 0
    monthly_cost_estimate_eur: tuple[float, float] = (0.0, 0.0)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_label": self.tier_label,
            "tier_summary": self.tier_summary,
            "orchestrator": self.orchestrator.to_dict(),
            "workers": [w.to_dict() for w in self.workers],
            "embedding": self.embedding.to_dict(),
            "estimated_tokens_per_sec": self.estimated_tokens_per_sec,
            "privacy_score": self.privacy_score,
            "monthly_cost_estimate_eur": list(self.monthly_cost_estimate_eur),
            "caveats": self.caveats,
        }


@dataclass
class ProfilerReport:
    """Top-level envelope written to JSON / rendered in the UI."""

    profile: SystemProfile
    recommendation: Recommendation
    generated_at: str
    tool_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "recommendation": self.recommendation.to_dict(),
            "generated_at": self.generated_at,
            "tool_version": self.tool_version,
        }
