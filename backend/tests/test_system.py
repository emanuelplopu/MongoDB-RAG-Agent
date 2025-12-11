"""
Unit tests for System Router.

Tests health checks, stats, and configuration endpoints.
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_root_health(self, client: TestClient):
        """Test root /health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_root_info(self, client: TestClient):
        """Test root / endpoint returns API info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data
        assert data["status"] == "running"

    def test_system_health(self, client: TestClient, mock_db):
        """Test /api/v1/system/health endpoint."""
        # Mock database ping
        mock_db.client.admin.command.return_value = {"ok": 1}
        
        response = client.get("/api/v1/system/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data


class TestSystemStats:
    """Test system statistics endpoints."""

    def test_system_info(self, client: TestClient):
        """Test /api/v1/system/info endpoint."""
        response = client.get("/api/v1/system/info")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data

    def test_system_config(self, client: TestClient):
        """Test /api/v1/system/config endpoint."""
        response = client.get("/api/v1/system/config")
        assert response.status_code == 200
        data = response.json()
        # Config should contain LLM settings
        assert "llm_provider" in data or "error" in data


class TestErrorHandling:
    """Test error handling."""

    def test_404_not_found(self, client: TestClient):
        """Test 404 response for unknown routes."""
        response = client.get("/api/v1/unknown/route")
        assert response.status_code == 404

    def test_method_not_allowed(self, client: TestClient):
        """Test 405 for wrong HTTP method."""
        response = client.post("/health")
        assert response.status_code == 405
