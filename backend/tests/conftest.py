"""
Backend Test Configuration.

Pytest fixtures and configuration for testing the FastAPI backend.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport


if "litellm" not in sys.modules:
    litellm_stub = ModuleType("litellm")

    async def _unexpected_acompletion(*args, **kwargs):
        """Fail fast if a test accidentally depends on a real LiteLLM call."""
        raise RuntimeError("litellm.acompletion was called without a test stub")

    litellm_stub.acompletion = _unexpected_acompletion
    litellm_stub.completion = _unexpected_acompletion
    litellm_stub.ModelResponse = SimpleNamespace
    sys.modules["litellm"] = litellm_stub


if "passlib.context" not in sys.modules:
    passlib_module = ModuleType("passlib")
    passlib_context_module = ModuleType("passlib.context")

    class _CryptContext:
        """Minimal test stub for passlib CryptContext."""

        def __init__(self, *args, **kwargs):
            pass

        def hash(self, password: str) -> str:
            return f"hashed::{password}"

        def verify(self, plain_password: str, hashed_password: str) -> bool:
            return hashed_password in {plain_password, f"hashed::{plain_password}"}

    passlib_context_module.CryptContext = _CryptContext
    passlib_module.context = passlib_context_module
    sys.modules["passlib"] = passlib_module
    sys.modules["passlib.context"] = passlib_context_module


if "jose" not in sys.modules:
    jose_module = ModuleType("jose")

    class _JWTError(Exception):
        """Minimal jose JWT error stub."""

    class _JWT:
        """Minimal jose.jwt stub."""

        @staticmethod
        def encode(payload, key, algorithm=None):
            return "test.jwt.token"

        @staticmethod
        def decode(token, key, algorithms=None):
            if token == "invalid":
                raise _JWTError("Invalid token")
            return {"sub": "test-user"}

    jose_module.JWTError = _JWTError
    jose_module.jwt = _JWT
    sys.modules["jose"] = jose_module


if "bcrypt" not in sys.modules:
    bcrypt_module = ModuleType("bcrypt")

    def _normalize_bytes(value: str | bytes) -> bytes:
        """Normalize string-like values into bytes for the bcrypt shim."""
        if isinstance(value, bytes):
            return value
        return value.encode("utf-8")

    def _gensalt(rounds: int = 12) -> bytes:
        """Return a deterministic test salt."""
        return f"salt::{rounds}".encode("utf-8")

    def _hashpw(password: bytes, salt: bytes) -> bytes:
        """Generate a deterministic password hash for tests."""
        password_bytes = _normalize_bytes(password)
        salt_bytes = _normalize_bytes(salt)
        return b"bcrypt::" + salt_bytes + b"::" + password_bytes

    def _checkpw(password: bytes, hashed: bytes) -> bool:
        """Validate a password against the deterministic test hash."""
        password_bytes = _normalize_bytes(password)
        hashed_bytes = _normalize_bytes(hashed)
        if not hashed_bytes.startswith(b"bcrypt::"):
            return False
        return hashed_bytes.endswith(b"::" + password_bytes)

    bcrypt_module.gensalt = _gensalt
    bcrypt_module.hashpw = _hashpw
    bcrypt_module.checkpw = _checkpw
    sys.modules["bcrypt"] = bcrypt_module

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
    class EmptyAsyncCursor:
        """Minimal async cursor supporting Mongo-style chaining in tests."""

        def __init__(self, documents: list[dict[str, Any]] | None = None):
            self._documents = documents or []

        def skip(self, count: int) -> "EmptyAsyncCursor":
            if count > 0:
                self._documents = self._documents[count:]
            return self

        def limit(self, count: int) -> "EmptyAsyncCursor":
            if count >= 0:
                self._documents = self._documents[:count]
            return self

        def sort(self, key: str, direction: int = 1) -> "EmptyAsyncCursor":
            reverse = direction == -1
            self._documents = sorted(
                self._documents,
                key=lambda doc: doc.get(key),
                reverse=reverse,
            )
            return self

        async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
            if length is None:
                return list(self._documents)
            return list(self._documents[:length])

        def __aiter__(self) -> "EmptyAsyncCursor":
            self._iterator = iter(self._documents)
            return self

        async def __anext__(self) -> dict[str, Any]:
            try:
                return next(self._iterator)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    db = MagicMock()
    db.client = MagicMock()

    # Mock collections with async-compatible methods
    mock_collection = MagicMock()
    mock_collection.count_documents = AsyncMock(return_value=0)
    mock_collection.estimated_document_count = AsyncMock(return_value=0)
    mock_collection.find = MagicMock(return_value=EmptyAsyncCursor())
    mock_collection.find_one = AsyncMock(return_value=None)
    mock_collection.replace_one = AsyncMock(return_value=MagicMock())
    mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test-id"))
    mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
    mock_collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    mock_collection.aggregate = MagicMock(return_value=EmptyAsyncCursor())

    db.db = MagicMock()
    db.db.__getitem__.return_value = mock_collection
    
    db.documents_collection = mock_collection
    db.chunks_collection = mock_collection
    
    # Mock client ping for health check
    db.client.admin.command = AsyncMock(return_value={"ok": 1})
    
    # Mock async methods
    db.connect = AsyncMock()
    db.disconnect = AsyncMock()
    db.switch_database = AsyncMock()
    
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
        llm_model="gpt-5.2",
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
    from backend.routers.auth import UserResponse, require_admin, require_auth

    async def _test_user_override() -> UserResponse:
        """Return a default authenticated test user."""
        return UserResponse(
            id="test-user-id",
            email="test@example.com",
            name="Test User",
            is_active=True,
            is_admin=True,
            created_at="2025-01-01T00:00:00",
        )
    
    # Set mock db on app state
    fastapi_app.state.db = mock_db
    fastapi_app.dependency_overrides[require_auth] = _test_user_override
    fastapi_app.dependency_overrides[require_admin] = _test_user_override

    yield fastapi_app

    fastapi_app.dependency_overrides.clear()


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
