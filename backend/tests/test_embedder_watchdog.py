"""Tests for the embedder watchdog status (``backend/services/embedder_watchdog.py``) and its endpoint.

The host agent writes the report; here a fake collection holds one. No database runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.routers.auth import require_admin
from backend.services.embedder_watchdog import (
    EMBEDDER_WATCHDOG_STATUS_ID,
    EmbedderWatchdogStatusResponse,
    embedder_watchdog_status,
    status_from_doc,
    target_from_doc,
)

NOW = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)


def agent_report(updated_at: datetime = NOW) -> Dict[str, Any]:
    """A report as ``EmbedderWatchdogWorker.WriteStatusAsync`` writes it (naive UTC datetimes, as PyMongo returns them)."""
    naive = updated_at.replace(tzinfo=None)
    return {
        "_id": EMBEDDER_WATCHDOG_STATUS_ID,
        "enabled": True,
        "check_seconds": 60,
        "thresholds": {
            "slow_ms": 5000, "probe_timeout_seconds": 30, "consecutive_checks": 5, "window_seconds": 240,
            "min_cpu_cores": 0.5, "warmup_seconds": 300, "min_restart_interval_seconds": 1800, "probe_words": 120,
        },
        "targets": [
            {
                "name": "embedder", "url": "http://127.0.0.1:11090/v1/embeddings",
                "containers": ["rag-embedder-gpu", "rag-embedder"], "container": "rag-embedder-gpu",
                "container_state": "running", "container_health": "healthy",
                "started_at": naive - timedelta(hours=3), "state": "healthy", "reason": "38 ms", "slow_checks": 0,
                "latency_ms": 38.2, "prompt_tokens": 153, "tokens_per_second": 4005.2, "cpu_cores": 0.12,
                "mem_bytes": 4_900_000_000, "last_check": naive, "last_error": None,
                "last_restart": naive - timedelta(hours=3, seconds=20),
                "last_restart_reason": "5 slow probe(s) over 4 min (last timeout after 30000 ms, mean CPU 0.98 cores)",
                "restart_count": 1, "next_restart_allowed_at": naive - timedelta(hours=2, minutes=30, seconds=20),
            },
            {
                "name": "embedder-query", "url": "http://127.0.0.1:11091/v1/embeddings",
                "containers": ["rag-embedder-query"], "container": None, "container_state": None,
                "container_health": None, "started_at": None, "state": "absent",
                "reason": "no configured container is running", "slow_checks": 0, "latency_ms": None,
                "prompt_tokens": None, "tokens_per_second": None, "cpu_cores": None, "mem_bytes": None,
                "last_check": naive, "last_error": None, "last_restart": None, "last_restart_reason": None,
                "restart_count": 0, "next_restart_allowed_at": None,
            },
        ],
        "agent_version": "1.2.0",
        "host": "MINISFORUM",
        "updated_at": naive,
    }


class FakeCollection:
    def __init__(self, doc: Optional[dict]) -> None:
        self.doc = doc
        self.filters: list = []

    async def find_one(self, filter: dict) -> Optional[dict]:
        self.filters.append(filter)
        return self.doc if self.doc and self.doc["_id"] == filter["_id"] else None


class TestStatusFromDoc:
    def test_no_report_yet(self):
        status = status_from_doc(None, NOW)
        assert status.reported is False
        assert status.agent_seen is False
        assert status.targets == []
        assert status.thresholds is None

    def test_fresh_report_is_typed(self):
        status = status_from_doc(agent_report(), NOW)
        assert status.reported is True
        assert status.agent_seen is True
        assert status.enabled is True
        assert status.check_seconds == 60
        assert status.agent_version == "1.2.0"
        assert status.updated_at == NOW
        assert status.thresholds is not None
        assert status.thresholds.slow_ms == 5000
        assert status.thresholds.consecutive_checks == 5
        assert status.thresholds.min_cpu_cores == 0.5
        embedder, query = status.targets
        assert embedder.name == "embedder"
        assert embedder.state == "healthy"
        assert embedder.container == "rag-embedder-gpu"
        assert embedder.latency_ms == 38.2
        assert embedder.tokens_per_second == 4005.2
        assert embedder.restart_count == 1
        assert embedder.last_restart == NOW - timedelta(hours=3, seconds=20)
        assert embedder.last_restart.tzinfo is not None
        assert "mean CPU 0.98 cores" in (embedder.last_restart_reason or "")
        assert query.state == "absent"
        assert query.container is None
        assert query.latency_ms is None
        assert query.restart_count == 0

    def test_stale_report_is_not_seen(self):
        status = status_from_doc(agent_report(NOW - timedelta(minutes=10)), NOW)
        assert status.reported is True
        assert status.agent_seen is False
        assert status.updated_at == NOW - timedelta(minutes=10)

    def test_malformed_values_do_not_break_the_response(self):
        doc = agent_report()
        doc["targets"][0]["latency_ms"] = "fast"
        doc["targets"][0]["restart_count"] = None
        doc["targets"].append("not a target")
        doc["thresholds"] = {}
        doc["enabled"] = "yes"
        status = status_from_doc(doc, NOW)
        assert len(status.targets) == 2
        assert status.targets[0].latency_ms is None
        assert status.targets[0].restart_count == 0
        assert status.thresholds is None
        assert status.enabled is None

    def test_target_defaults(self):
        target = target_from_doc({"name": "x"})
        assert target.state == "unknown"
        assert target.containers == []
        assert target.slow_checks == 0


class TestEmbedderWatchdogStatus:
    @pytest.mark.asyncio
    async def test_reads_the_watchdog_document(self):
        collection = FakeCollection(agent_report())
        status = await embedder_watchdog_status(collection, NOW)
        assert collection.filters == [{"_id": EMBEDDER_WATCHDOG_STATUS_ID}]
        assert status.agent_seen is True
        assert [t.name for t in status.targets] == ["embedder", "embedder-query"]

    @pytest.mark.asyncio
    async def test_without_document(self):
        status = await embedder_watchdog_status(FakeCollection(None), NOW)
        assert status.reported is False


class TestEndpoint:
    """``GET /api/v1/system/embedder-watchdog``."""

    def test_returns_the_report_for_the_admin(self, client: TestClient, app):
        app.state.telemetry_store = SimpleNamespace(db={"host_control": FakeCollection(agent_report(datetime.now(timezone.utc)))})
        try:
            response = client.get("/api/v1/system/embedder-watchdog")
        finally:
            app.state.telemetry_store = None
        assert response.status_code == 200
        data = response.json()
        assert data["reported"] is True
        assert data["agent_seen"] is True
        assert data["thresholds"]["consecutive_checks"] == 5
        assert data["targets"][0]["name"] == "embedder"
        assert data["targets"][0]["state"] == "healthy"
        assert data["targets"][0]["restart_count"] == 1
        assert data["targets"][1]["state"] == "absent"
        EmbedderWatchdogStatusResponse.model_validate(data)

    def test_503_while_telemetry_history_is_off(self, client: TestClient, app):
        app.state.telemetry_store = None
        response = client.get("/api/v1/system/embedder-watchdog")
        assert response.status_code == 503

    def test_requires_admin(self, app):
        async def _forbidden() -> None:
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = _forbidden
        try:
            response = TestClient(app).get("/api/v1/system/embedder-watchdog")
        finally:
            app.dependency_overrides.pop(require_admin, None)
        assert response.status_code == 403
