"""``docker-compose.gpu.yml`` and the ``INGEST_GPU_SLOTS`` forwarding in ``docker-compose.yml``.

The GPU layer must reserve the NVIDIA device for exactly the services that convert
documents, with the same block the ``embedder-gpu`` profile uses, and every service that
forwards the conversion-pool settings must forward the GPU slot count too (a service
that misses it would silently convert on the CPU).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
CONVERSION_SERVICES = {"backend", "backend-quellex", "ingestion-worker", "cli"}


def _services(name: str) -> Dict[str, Dict[str, Any]]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]


def test_gpu_layer_reserves_the_gpu_for_the_conversion_services() -> None:
    base = _services("docker-compose.yml")
    gpu = _services("docker-compose.gpu.yml")
    expected = base["embedder-gpu"]["deploy"]["resources"]["reservations"]["devices"]
    assert expected == [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]
    assert set(gpu) == CONVERSION_SERVICES
    for name, service in gpu.items():
        assert name in base, name
        assert set(service) == {"deploy"}, name  # the layer adds the reservation and nothing else
        assert service["deploy"]["resources"]["reservations"]["devices"] == expected, name


def test_every_conversion_service_forwards_the_gpu_slots() -> None:
    base = _services("docker-compose.yml")
    forwarding = {
        name
        for name, service in base.items()
        if any(str(entry).startswith("INGEST_DOCLING_THREADS=") for entry in service.get("environment", []))
    }
    assert forwarding == CONVERSION_SERVICES
    for name in forwarding:
        assert "INGEST_GPU_SLOTS=${INGEST_GPU_SLOTS:-}" in base[name]["environment"], name
