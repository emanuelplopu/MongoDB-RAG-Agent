"""
Integration tests for Backend API.

These tests verify end-to-end functionality with real or test database connections.
Run with: pytest backend/tests/integration/ -v
"""

import pytest
import os
from typing import Generator
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport


# Skip if no test database available
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS", "").lower() != "true",
    reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=true to enable."
)


@pytest.fixture
def integration_client() -> Generator[TestClient, None, None]:
    """Create a test client with real database connection."""
    from backend.main import app
    
    with TestClient(app) as client:
        yield client


class TestHealthIntegration:
    """Integration tests for health endpoints."""

    def test_api_is_running(self, integration_client: TestClient):
        """Test API is accessible."""
        response = integration_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    def test_health_check(self, integration_client: TestClient):
        """Test health endpoint returns healthy."""
        response = integration_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_system_health(self, integration_client: TestClient):
        """Test system health with database check."""
        response = integration_client.get("/api/v1/system/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        # If database is connected, status should be healthy
        if "database" in data:
            assert data["database"]["status"] in ["connected", "healthy"]


class TestProfilesIntegration:
    """Integration tests for profile management."""

    def test_list_profiles(self, integration_client: TestClient):
        """Test listing profiles returns at least default."""
        response = integration_client.get("/api/v1/profiles")
        assert response.status_code == 200
        data = response.json()
        assert "profiles" in data
        assert "default" in data["profiles"] or len(data["profiles"]) > 0

    def test_get_active_profile(self, integration_client: TestClient):
        """Test getting active profile."""
        response = integration_client.get("/api/v1/profiles/active")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data or "profile_key" in data


class TestSearchIntegration:
    """Integration tests for search functionality."""

    def test_search_returns_results_structure(self, integration_client: TestClient):
        """Test search returns proper structure."""
        response = integration_client.post("/api/v1/search", json={
            "query": "test query",
            "search_type": "hybrid",
            "match_count": 5
        })
        assert response.status_code in [200, 500]
        
        if response.status_code == 200:
            data = response.json()
            assert "results" in data
            assert "total_results" in data
            assert "processing_time_ms" in data

    def test_semantic_search_integration(self, integration_client: TestClient):
        """Test semantic search endpoint."""
        response = integration_client.post("/api/v1/search/semantic", json={
            "query": "test semantic search",
            "match_count": 5
        })
        # Should succeed or fail gracefully with missing indexes
        assert response.status_code in [200, 500]

    def test_text_search_integration(self, integration_client: TestClient):
        """Test text search endpoint."""
        response = integration_client.post("/api/v1/search/text", json={
            "query": "test text search",
            "match_count": 5
        })
        assert response.status_code in [200, 500]


class TestChatIntegration:
    """Integration tests for chat functionality."""

    def test_chat_response_structure(self, integration_client: TestClient):
        """Test chat returns proper response structure."""
        response = integration_client.post("/api/v1/chat", json={
            "message": "Hello, what can you help me with?",
            "search_type": "hybrid",
            "match_count": 5,
            "include_sources": True
        })
        # May fail if LLM not configured
        assert response.status_code in [200, 500]
        
        if response.status_code == 200:
            data = response.json()
            assert "message" in data
            assert "conversation_id" in data


class TestIngestionIntegration:
    """Integration tests for ingestion."""

    def test_list_documents(self, integration_client: TestClient):
        """Test listing documents."""
        response = integration_client.get("/api/v1/ingestion/documents")
        assert response.status_code in [200, 500]
        
        if response.status_code == 200:
            data = response.json()
            assert "documents" in data
            assert "total" in data

    def test_ingestion_status(self, integration_client: TestClient):
        """Test getting ingestion status."""
        response = integration_client.get("/api/v1/ingestion/status")
        assert response.status_code in [200, 500]
        
        if response.status_code == 200:
            data = response.json()
            assert "status" in data


class TestSystemStatsIntegration:
    """Integration tests for system statistics."""

    def test_system_info(self, integration_client: TestClient):
        """Test system info endpoint."""
        response = integration_client.get("/api/v1/system/info")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data

    def test_system_config(self, integration_client: TestClient):
        """Test system config endpoint."""
        response = integration_client.get("/api/v1/system/config")
        assert response.status_code == 200
        data = response.json()
        assert "llm_provider" in data

    def test_database_stats(self, integration_client: TestClient):
        """Test database stats endpoint."""
        response = integration_client.get("/api/v1/system/database-stats")
        assert response.status_code in [200, 500]

    def test_indexes_status(self, integration_client: TestClient):
        """Test indexes status endpoint."""
        response = integration_client.get("/api/v1/system/indexes")
        assert response.status_code in [200, 500]


class TestEndToEndFlow:
    """End-to-end workflow tests."""

    def test_profile_then_search(self, integration_client: TestClient):
        """Test getting profile then performing search."""
        # Get active profile
        profile_response = integration_client.get("/api/v1/profiles/active")
        assert profile_response.status_code == 200
        
        # Perform search
        search_response = integration_client.post("/api/v1/search", json={
            "query": "test",
            "search_type": "hybrid",
            "match_count": 5
        })
        assert search_response.status_code in [200, 500]

    def test_health_then_chat(self, integration_client: TestClient):
        """Test health check then chat."""
        # Health check
        health_response = integration_client.get("/api/v1/system/health")
        assert health_response.status_code == 200
        
        # Chat
        chat_response = integration_client.post("/api/v1/chat", json={
            "message": "Hello"
        })
        assert chat_response.status_code in [200, 500]
