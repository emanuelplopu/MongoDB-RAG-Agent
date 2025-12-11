"""
Backend Test Configuration.

Pytest fixtures and configuration for testing the FastAPI backend.
"""

import asyncio
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

# Test configuration
TEST_MONGODB_URI = "mongodb://localhost:27017/?directConnection=true"
TEST_DATABASE = "test_rag_db"


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_db():
    """Create a mock database manager with async-compatible methods."""
    db = MagicMock()
    db.client = MagicMock()
    db.db = MagicMock()
    
    # Mock collections with async-compatible methods
    mock_collection = MagicMock()
    mock_collection.count_documents = AsyncMock(return_value=0)
    mock_collection.find = MagicMock(return_value=MagicMock(
        skip=MagicMock(return_value=MagicMock(
            limit=MagicMock(return_value=MagicMock(
                to_list=AsyncMock(return_value=[])
            ))
        ))
    ))
    mock_collection.find_one = AsyncMock(return_value=None)
    mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
    mock_collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    
    db.documents_collection = mock_collection
    db.chunks_collection = mock_collection
    
    # Mock client ping for health check
    db.client.admin.command = AsyncMock(return_value={"ok": 1})
    
    # Mock async methods
    db.connect = AsyncMock()
    db.disconnect = AsyncMock()
    
    return db


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    from backend.core.config import BackendSettings
    
    settings = BackendSettings(
        mongodb_uri=TEST_MONGODB_URI,
        mongodb_database=TEST_DATABASE,
        llm_provider="openai",
        llm_api_key="test-key",
        llm_model="gpt-4.1-mini",
        embedding_provider="openai",
        embedding_api_key="test-key",
        embedding_model="text-embedding-3-small",
        cors_origins=["*"],
        debug=True,
    )
    return settings


@pytest.fixture
def app(mock_db):
    """Create a test FastAPI application with mock database.
    
    Tests should handle potential errors from database operations gracefully.
    """
    from backend.main import app as fastapi_app
    
    # Set mock db on app state
    fastapi_app.state.db = mock_db
    
    return fastapi_app


@pytest.fixture
def client(app) -> TestClient:
    """Create a synchronous test client."""
    return TestClient(app)


@pytest.fixture
async def async_client(app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# Sample test data
@pytest.fixture
def sample_document():
    """Sample document for testing."""
    return {
        "_id": "doc_123",
        "title": "Test Document",
        "source": "/path/to/test.pdf",
        "chunks_count": 5,
        "created_at": "2025-01-01T00:00:00Z",
        "metadata": {"author": "Test Author"}
    }


@pytest.fixture
def sample_chunk():
    """Sample chunk for testing."""
    return {
        "_id": "chunk_456",
        "document_id": "doc_123",
        "content": "This is test content for the chunk.",
        "embedding": [0.1] * 1536,
        "metadata": {"page": 1}
    }


@pytest.fixture
def sample_search_results():
    """Sample search results for testing."""
    return [
        {
            "chunk_id": "chunk_1",
            "document_id": "doc_1",
            "document_title": "Document 1",
            "document_source": "/path/doc1.pdf",
            "content": "First result content",
            "similarity": 0.95,
            "metadata": {}
        },
        {
            "chunk_id": "chunk_2",
            "document_id": "doc_2",
            "document_title": "Document 2",
            "document_source": "/path/doc2.pdf",
            "content": "Second result content",
            "similarity": 0.85,
            "metadata": {}
        }
    ]


@pytest.fixture
def sample_profile():
    """Sample profile for testing."""
    return {
        "name": "Test Profile",
        "description": "A test profile",
        "documents_folders": ["./test_docs"],
        "database": "test_db",
        "collection_documents": "documents",
        "collection_chunks": "chunks",
        "vector_index": "vector_index",
        "text_index": "text_index"
    }
