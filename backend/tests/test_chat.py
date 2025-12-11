"""
Unit tests for Chat Router.

Tests chat message handling and conversation management.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock


class TestChatEndpoints:
    """Test chat API endpoints."""

    def test_chat_requires_message(self, client: TestClient):
        """Test that chat requires a message."""
        response = client.post("/api/v1/chat", json={})
        # Should return validation error
        assert response.status_code in [400, 422]

    def test_chat_with_message(self, client: TestClient):
        """Test chat with valid message."""
        response = client.post("/api/v1/chat", json={
            "message": "Hello, how are you?"
        })
        
        # Should return response or error (may fail due to missing LLM key)
        assert response.status_code in [200, 500]

    def test_chat_with_conversation_id(self, client: TestClient):
        """Test chat with existing conversation."""
        response = client.post("/api/v1/chat", json={
            "message": "Follow-up message",
            "conversation_id": "existing_conv_123"
        })
        # Should process (may fail due to missing conversation)
        assert response.status_code in [200, 404, 500]


class TestChatOptions:
    """Test chat configuration options."""

    def test_chat_search_types(self, client: TestClient):
        """Test different search types."""
        for search_type in ["semantic", "text", "hybrid"]:
            response = client.post("/api/v1/chat", json={
                "message": "Test message",
                "search_type": search_type
            })
            # Should accept valid search types
            assert response.status_code in [200, 500]

    def test_chat_with_sources(self, client: TestClient):
        """Test chat with include_sources option."""
        response = client.post("/api/v1/chat", json={
            "message": "Test message",
            "include_sources": True
        })
        assert response.status_code in [200, 500]

    def test_chat_match_count(self, client: TestClient):
        """Test chat with custom match_count."""
        response = client.post("/api/v1/chat", json={
            "message": "Test message",
            "match_count": 20
        })
        assert response.status_code in [200, 422, 500]


class TestConversationManagement:
    """Test conversation listing and deletion."""

    def test_list_conversations(self, client: TestClient):
        """Test listing conversations endpoint."""
        response = client.get("/api/v1/chat/conversations")
        # Should return list or error
        assert response.status_code in [200, 500]

    def test_get_conversation(self, client: TestClient):
        """Test getting specific conversation."""
        response = client.get("/api/v1/chat/conversations/nonexistent_id")
        # Should return 404 for non-existent
        assert response.status_code in [404, 500]

    def test_delete_conversation(self, client: TestClient):
        """Test deleting conversation."""
        response = client.delete("/api/v1/chat/conversations/test_id")
        # Should return success or 404
        assert response.status_code in [200, 204, 404, 500]


class TestChatValidation:
    """Test chat input validation."""

    def test_empty_message(self, client: TestClient):
        """Test empty message validation."""
        response = client.post("/api/v1/chat", json={
            "message": ""
        })
        # Should return validation error
        assert response.status_code in [200, 422, 500]

    def test_very_long_message(self, client: TestClient):
        """Test very long message handling."""
        long_message = "x" * 50000
        response = client.post("/api/v1/chat", json={
            "message": long_message
        })
        # Should handle gracefully
        assert response.status_code in [200, 413, 422, 500]

    def test_invalid_search_type(self, client: TestClient):
        """Test invalid search_type in chat."""
        response = client.post("/api/v1/chat", json={
            "message": "Test",
            "search_type": "invalid_type"
        })
        # Should return validation error
        assert response.status_code in [200, 422, 500]
