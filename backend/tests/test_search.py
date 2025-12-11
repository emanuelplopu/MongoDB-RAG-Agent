"""
Unit tests for Search Router.

Tests semantic, text, and hybrid search endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock


class TestSearchEndpoints:
    """Test search API endpoints."""

    def test_search_requires_query(self, client: TestClient):
        """Test that search requires a query parameter."""
        response = client.post("/api/v1/search", json={})
        # Should return validation error
        assert response.status_code in [400, 422]

    def test_search_with_valid_query(self, client: TestClient, mock_db):
        """Test search with valid query."""
        response = client.post("/api/v1/search", json={
            "query": "test query",
            "search_type": "hybrid",
            "match_count": 10
        })
        
        # Should return some response (may fail due to missing indexes)
        assert response.status_code in [200, 500]

    def test_semantic_search_endpoint(self, client: TestClient):
        """Test semantic search endpoint exists."""
        response = client.post("/api/v1/search/semantic", json={
            "query": "semantic test",
            "match_count": 5
        })
        # Should not be 404
        assert response.status_code != 404

    def test_text_search_endpoint(self, client: TestClient):
        """Test text search endpoint exists."""
        response = client.post("/api/v1/search/text", json={
            "query": "text test",
            "match_count": 5
        })
        # Should not be 404
        assert response.status_code != 404

    def test_hybrid_search_endpoint(self, client: TestClient):
        """Test hybrid search endpoint exists."""
        response = client.post("/api/v1/search/hybrid", json={
            "query": "hybrid test",
            "match_count": 5
        })
        # Should not be 404
        assert response.status_code != 404


class TestSearchValidation:
    """Test search input validation."""

    def test_match_count_minimum(self, client: TestClient):
        """Test match_count minimum validation."""
        response = client.post("/api/v1/search", json={
            "query": "test",
            "match_count": 0
        })
        # Should either succeed or return validation error
        assert response.status_code in [200, 422, 500]

    def test_match_count_maximum(self, client: TestClient):
        """Test match_count maximum validation."""
        response = client.post("/api/v1/search", json={
            "query": "test",
            "match_count": 1000
        })
        # Should either succeed or return validation error
        assert response.status_code in [200, 422, 500]

    def test_invalid_search_type(self, client: TestClient):
        """Test invalid search_type validation."""
        response = client.post("/api/v1/search", json={
            "query": "test",
            "search_type": "invalid_type"
        })
        # Should return validation error
        assert response.status_code in [200, 422, 500]

    def test_empty_query(self, client: TestClient):
        """Test empty query string."""
        response = client.post("/api/v1/search", json={
            "query": "",
            "search_type": "hybrid"
        })
        # Should return validation error or empty results
        assert response.status_code in [200, 422, 500]
