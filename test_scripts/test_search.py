"""Test search functions with MongoDB database."""

import asyncio
from src.dependencies import AgentDependencies
from src.tools import semantic_search, hybrid_search


class MockContext:
    """Mock context for search functions."""
    def __init__(self, deps):
        self.deps = deps


async def test_database_connection():
    """Test MongoDB connection and check for data."""
    print("Testing MongoDB connection...")
    deps = AgentDependencies()
    await deps.initialize()

    # Check chunk count
    chunk_count = await deps.db.chunks.count_documents({})
    doc_count = await deps.db.documents.count_documents({})

    print(f"Documents in database: {doc_count}")
    print(f"Chunks in database: {chunk_count}")

    await deps.cleanup()
    return chunk_count > 0


async def test_semantic_search():
    """Test semantic search."""
    print("\nTesting semantic search...")
    deps = AgentDependencies()
    await deps.initialize()
    ctx = MockContext(deps)

    results = await semantic_search(ctx, 'test query', match_count=5)
    print(f"Semantic search returned {len(results)} results")

    if results:
        print(f"Top result: {results[0].document_title} (similarity: {results[0].similarity:.3f})")

    await deps.cleanup()
    return len(results) > 0


async def test_hybrid_search():
    """Test hybrid search."""
    print("\nTesting hybrid search...")
    deps = AgentDependencies()
    await deps.initialize()
    ctx = MockContext(deps)

    results = await hybrid_search(ctx, 'MongoDB vector search', match_count=5)
    print(f"Hybrid search returned {len(results)} results")

    if results:
        for i, r in enumerate(results[:3], 1):
            print(f"{i}. {r.document_title} - {r.similarity:.3f}")

    await deps.cleanup()
    return len(results) > 0


async def main():
    """Run all tests."""
    print("=" * 60)
    print("RecallHub - Search Function Tests")
    print("=" * 60)

    try:
        # Test connection
        has_data = await test_database_connection()
        if not has_data:
            print("\n[WARNING] No data in database. Run ingestion first:")
            print("  uv run python -m src.ingestion.ingest -d documents")
            return

        # Test searches
        await test_semantic_search()
        await test_hybrid_search()

        print("\n" + "=" * 60)
        print("All tests completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
