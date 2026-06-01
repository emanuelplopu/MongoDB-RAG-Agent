"""Tests for the Strategy OS telemetry router.

Covers the read-only admin endpoints exposed under
``/api/v1/admin/telemetry/strategy``. The tests rely on the
``mock_db`` fixture provided by ``backend/tests/conftest.py`` which
wires a single :class:`MagicMock` collection onto ``app.state.db.db``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


class _FakeCursor:
    """Minimal async cursor that supports the chained calls we use."""

    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs) -> "_FakeCursor":
        return self

    def skip(self, count: int) -> "_FakeCursor":
        if count > 0:
            self._docs = self._docs[count:]
        return self

    def limit(self, count: int) -> "_FakeCursor":
        if count is not None and count >= 0:
            self._docs = self._docs[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


def _wire_collection(
    mock_db: MagicMock,
    *,
    find_docs: list[dict[str, Any]] | None = None,
    find_one_doc: dict[str, Any] | None = None,
) -> MagicMock:
    """Replace the shared mock collection so the router sees test data."""

    coll = MagicMock()
    coll.find = MagicMock(return_value=_FakeCursor(find_docs or []))
    coll.find_one = AsyncMock(return_value=find_one_doc)
    mock_db.db.__getitem__.return_value = coll
    return coll


def test_runs_returns_empty_list_when_no_data(client: TestClient, mock_db):
    """GET /runs returns an empty list when no runs exist."""

    _wire_collection(mock_db, find_docs=[])

    response = client.get("/api/v1/admin/telemetry/strategy/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"] == []
    assert payload["total"] == 0
    assert payload["limit"] == 50
    assert payload["offset"] == 0


def test_runs_with_date_filter_passes_through(client: TestClient, mock_db):
    """GET /runs forwards ``since`` / ``until`` into the Mongo query."""

    coll = _wire_collection(
        mock_db,
        find_docs=[
            {
                "trace_id": "t-1",
                "run_id": "r-1",
                "strategy_id": "structured",
                "status": "completed",
                "started_at": datetime(2026, 5, 18, tzinfo=timezone.utc),
                "duration_ms": 1234,
            }
        ],
    )

    since = "2026-05-01T00:00:00+00:00"
    until = "2026-05-19T00:00:00+00:00"
    response = client.get(
        "/api/v1/admin/telemetry/strategy/runs",
        params={
            "since": since,
            "until": until,
            "strategy_id": "structured",
            "status": "completed",
            "limit": 25,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["runs"][0]["trace_id"] == "t-1"

    # The router must build a bounded query that includes both range
    # endpoints and the explicit filters.
    coll.find.assert_called_once()
    query = coll.find.call_args.args[0]
    assert "$gte" in query["started_at"]
    assert "$lte" in query["started_at"]
    assert query["strategy_id"] == "structured"
    assert query["status"] == "completed"


def test_run_by_trace_id_returns_404_when_missing(client: TestClient, mock_db):
    """GET /runs/{trace_id} returns 404 when the trace is unknown."""

    _wire_collection(mock_db, find_one_doc=None)

    response = client.get("/api/v1/admin/telemetry/strategy/runs/missing-trace")

    assert response.status_code == 404
    body = response.json()
    assert "missing-trace" in str(body)


def test_llm_calls_returns_calls_for_trace(client: TestClient, mock_db):
    """GET /runs/{trace_id}/llm-calls returns calls sorted by execution."""

    docs = [
        {
            "call_id": "c-1",
            "trace_id": "t-1",
            "node_id": "plan",
            "node_type": "planner",
            "model": "gpt-5.2",
            "provider": "openai",
            "user_prompt": "hello",
            "assistant_response": "hi",
            "latency_ms": 120,
            "success": True,
            "error": None,
            "created_at": datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        },
        {
            "call_id": "c-2",
            "trace_id": "t-1",
            "node_id": "synth",
            "node_type": "synthesizer",
            "model": "gpt-5.2",
            "provider": "openai",
            "user_prompt": "summarize",
            "assistant_response": "ok",
            "latency_ms": 250,
            "success": True,
            "error": None,
            "created_at": datetime(2026, 5, 18, 10, 1, tzinfo=timezone.utc),
        },
    ]
    coll = _wire_collection(mock_db, find_docs=docs)

    response = client.get(
        "/api/v1/admin/telemetry/strategy/runs/t-1/llm-calls"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "t-1"
    assert payload["total"] == 2
    assert [c["call_id"] for c in payload["calls"]] == ["c-1", "c-2"]
    coll.find.assert_called_once_with({"trace_id": "t-1"})


def test_decisions_returns_empty_list_when_no_data(
    client: TestClient, mock_db
):
    """GET /decisions returns an empty list when no decisions exist."""

    _wire_collection(mock_db, find_docs=[])

    response = client.get("/api/v1/admin/telemetry/strategy/decisions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decisions"] == []
    assert payload["total"] == 0
    assert payload["limit"] == 50
    assert payload["offset"] == 0


def test_stats_returns_correct_shape(client: TestClient, mock_db):
    """GET /stats returns aggregate fields with the expected shape."""

    now = datetime.now(timezone.utc)
    runs = [
        {
            "trace_id": "t-1",
            "strategy_id": "structured",
            "status": "completed",
            "started_at": now - timedelta(hours=1),
            "duration_ms": 1000,
            "llm_call_count": 2,
        },
        {
            "trace_id": "t-2",
            "strategy_id": "structured",
            "status": "failed",
            "started_at": now - timedelta(hours=2),
            "duration_ms": 500,
            "llm_call_ids": ["a", "b", "c"],
        },
        {
            "trace_id": "t-3",
            "strategy_id": "exploratory",
            "status": "completed",
            "started_at": now - timedelta(hours=3),
            "duration_ms": 1500,
            "llm_call_count": 1,
        },
    ]
    _wire_collection(mock_db, find_docs=runs)

    response = client.get(
        "/api/v1/admin/telemetry/strategy/stats", params={"range": "7d"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["range"] == "7d"
    assert payload["total_runs"] == 3
    # Two of three runs completed successfully.
    assert payload["success_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert payload["avg_duration_ms"] == pytest.approx(1000.0, rel=1e-3)
    # 2 + 3 + 1 = 6 LLM calls across 3 runs.
    assert payload["avg_llm_calls"] == pytest.approx(2.0, rel=1e-3)
    top = payload["top_strategies"]
    assert isinstance(top, list)
    assert top[0] == {"strategy_id": "structured", "count": 2}
    assert {"strategy_id": "exploratory", "count": 1} in top


def test_stats_rejects_invalid_range(client: TestClient, mock_db):
    """GET /stats rejects unknown range tokens with 400."""

    _wire_collection(mock_db, find_docs=[])

    response = client.get(
        "/api/v1/admin/telemetry/strategy/stats", params={"range": "bogus"}
    )

    assert response.status_code == 400
