"""Live check for finding F25: an identifier question gets the Firmenbuchabfrage at rank 1.

Runs only against a live backend that holds the diejuristin corpus and skips otherwise::

    $env:RUN_INTEGRATION_TESTS = "true"
    $env:TEST_BASE_URL = "http://127.0.0.1:11001"   # Quellex backend (default)
    $env:TEST_API_KEY = "<API key of an account with profile access>"
    uv run pytest backend/tests/integration/test_identifier_search_live.py -v

``TEST_API_KEY`` is sent as ``X-API-Key``; the Quellex backend runs with login
enforcement, so the test skips without one when the backend answers 401.
See ``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md`` (F25, F17).
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://127.0.0.1:11001")
API_KEY = os.getenv("TEST_API_KEY", "")
QUERY = "ALPO CONSULT GmbH FN 404945h Geschäftszweig"
EXPECTED_SOURCE_FRAGMENT = "Firmenbuchabfrage, FN 404945h"

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS", "").lower() != "true",
    reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=true to enable.",
)


@pytest.fixture(scope="module")
def live_client():
    """HTTP client for the live backend; skips the module when none answers."""
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    client = httpx.Client(base_url=BASE_URL, headers=headers, timeout=60.0)
    try:
        health = client.get("/health", timeout=3.0)
    except httpx.HTTPError as exc:
        client.close()
        pytest.skip(f"No live backend at {BASE_URL}: {exc}")
    if health.status_code != 200:
        client.close()
        pytest.skip(f"Backend at {BASE_URL} is not healthy: {health.status_code}")
    yield client
    client.close()


def test_firmenbuch_query_returns_the_firmenbuchabfrage_first(live_client: httpx.Client) -> None:
    response = live_client.post(
        "/api/v1/search/hybrid",
        json={"query": QUERY, "search_type": "hybrid", "match_count": 10},
    )
    if response.status_code == 401:
        pytest.skip("Backend requires login; set TEST_API_KEY")
    assert response.status_code == 200, response.text

    results = response.json()["results"]
    assert results, "hybrid search returned nothing"
    top = results[0]
    assert EXPECTED_SOURCE_FRAGMENT in top["document_source"], (
        f"rank 1 is {top['document_source']!r}; sources: {[r['document_source'] for r in results]}"
    )
    assert top["metadata"].get("exact_identifier_match") is True
    # Copies of the same file never take more than one slot.
    hashes_seen = [
        (r["document_source"], r["metadata"].get("locations", []))
        for r in results
    ]
    assert len({source for source, _ in hashes_seen}) == len(hashes_seen)
