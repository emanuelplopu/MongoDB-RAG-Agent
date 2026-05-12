"""Golden tests for tier classification and model recommendations."""

from __future__ import annotations

import pytest

from quellex_profiler.schema import GpuInfo, SystemProfile
from quellex_profiler.recommend import recommend_config
from quellex_profiler.recommend.tiers import classify_tier


def _profile(
    *,
    ram: float,
    gpus: list[GpuInfo] | None = None,
    apple: bool = False,
    arch: str = "x86_64",
    os_name: str = "Linux",
) -> SystemProfile:
    gpus = gpus or []
    if apple and ram > 0:
        vram = round(ram * 0.75, 1)
    else:
        vram = round(sum(g.vram_gb for g in gpus if not g.is_integrated), 1)
    return SystemProfile(
        os_name=os_name,
        os_version="test",
        arch=arch,
        cpu_brand="Test CPU",
        cpu_vendor="intel",
        cpu_cores_physical=8,
        cpu_cores_logical=16,
        cpu_max_ghz=3.5,
        cpu_flags=["avx2"],
        ram_gb=ram,
        gpus=gpus,
        total_accel_vram_gb=vram,
        is_apple_silicon=apple,
    )


def _nvidia(model: str, vram: float, cc: str = "8.6") -> GpuInfo:
    return GpuInfo(vendor="nvidia", model=model, vram_gb=vram, compute_capability=cc)


def _intel_igpu() -> GpuInfo:
    return GpuInfo(vendor="intel", model="Intel UHD Graphics 630",
                   vram_gb=1.0, is_integrated=True)


# ---------- Tier classification ----------

@pytest.mark.parametrize(
    "p, expected_tier",
    [
        (_profile(ram=8, gpus=[_intel_igpu()]), "cloud-only"),
        (_profile(ram=16, gpus=[_nvidia("RTX 3050", 6.0)]), "hybrid-small"),
        (_profile(ram=32, gpus=[_nvidia("RTX 4070", 12.0)]), "local-balanced"),
        (_profile(ram=64, gpus=[_nvidia("RTX 4090", 24.0)]), "local-premium"),
        (_profile(ram=128, gpus=[_nvidia("RTX 6000 Ada", 48.0)]), "workstation"),
        # Apple Silicon M3 Max 64GB -> local-premium (75% of 64 = 48)
        (_profile(ram=64, apple=True, arch="arm64", os_name="macOS 14.5"),
         "local-premium"),
        # Apple Silicon M3 16GB -> hybrid (small unified mem)
        (_profile(ram=16, apple=True, arch="arm64"), "hybrid-small"),
        # Old Pascal (cc 6.1) with 24GB should downgrade from premium to balanced
        (_profile(ram=64, gpus=[_nvidia("TITAN Xp", 24.0, cc="6.1")]),
         "local-balanced"),
    ],
)
def test_tier_classification(p, expected_tier):
    assert classify_tier(p).key == expected_tier


# ---------- Full recommendation smoke tests ----------

def test_recommend_cloud_only_low_ram():
    rec = recommend_config(_profile(ram=8, gpus=[_intel_igpu()]))
    assert rec.tier == "cloud-only"
    assert rec.orchestrator.runs_locally is False
    assert rec.workers[0].runs_locally is False
    assert rec.embedding.runs_locally is False
    assert rec.privacy_score == 0
    assert any("leave the network" in c for c in rec.caveats)


def test_recommend_hybrid_small():
    rec = recommend_config(_profile(ram=16, gpus=[_nvidia("RTX 3050", 6.0)]))
    assert rec.tier == "hybrid-small"
    assert rec.orchestrator.runs_locally is False  # cloud orchestrator
    assert rec.workers[0].runs_locally is True      # local worker
    assert rec.embedding.runs_locally is True       # local embeddings
    # Embeddings local => privacy score >= 50
    assert rec.privacy_score >= 50


def test_recommend_local_balanced_fully_local():
    rec = recommend_config(_profile(ram=32, gpus=[_nvidia("RTX 4070", 12.0)]))
    assert rec.tier == "local-balanced"
    assert rec.orchestrator.runs_locally is True
    assert rec.workers[0].runs_locally is True
    assert rec.embedding.runs_locally is True
    assert rec.privacy_score == 100
    assert rec.monthly_cost_estimate_eur == (0.0, 0.0)


def test_recommend_workstation_uses_70b_orchestrator():
    rec = recommend_config(_profile(ram=128, gpus=[_nvidia("RTX 6000 Ada", 48.0)]))
    assert rec.tier == "workstation"
    # 70B-class model
    assert "70b" in rec.orchestrator.model_id or "72b" in rec.orchestrator.model_id
    assert rec.privacy_score == 100


def test_recommend_apple_silicon_m3_max():
    p = _profile(ram=64, apple=True, arch="arm64", os_name="macOS 14.5")
    rec = recommend_config(p)
    assert rec.tier == "local-premium"
    assert rec.orchestrator.runs_locally is True


def test_recommend_amd_dgpu_adds_caveat():
    p = _profile(
        ram=32,
        gpus=[GpuInfo(vendor="amd", model="AMD Radeon RX 7800 XT", vram_gb=16.0)],
    )
    rec = recommend_config(p)
    assert any("AMD" in c for c in rec.caveats)


def test_recommend_arm64_non_apple_caveat():
    p = _profile(ram=16, arch="arm64", os_name="Ubuntu 22.04", apple=False)
    rec = recommend_config(p)
    assert any("ARM64" in c for c in rec.caveats)


def test_recommendation_serialisable():
    rec = recommend_config(_profile(ram=32, gpus=[_nvidia("RTX 4070", 12.0)]))
    d = rec.to_dict()
    assert d["tier"] == "local-balanced"
    assert isinstance(d["workers"], list)
    assert "model_id" in d["orchestrator"]
