"""Tenant label of the shared ingestion worker's telemetry (docs/12-TELEMETRY_SYSTEM.md)."""

from __future__ import annotations

import pytest

worker_module = pytest.importorskip("backend.workers.ingestion_worker")


def test_worker_without_tenant_id_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TENANT_ID", raising=False)
    assert worker_module.worker_telemetry_tenant() == "shared"


def test_worker_blank_tenant_id_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ID", "  ")
    assert worker_module.worker_telemetry_tenant() == "shared"


def test_worker_explicit_tenant_id_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ID", "quellex")
    assert worker_module.worker_telemetry_tenant() == "quellex"
