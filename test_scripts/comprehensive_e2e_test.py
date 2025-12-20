"""Comprehensive End-to-End Testing of RecallHub.

Tests:
1. Full ingestion pipeline validation
2. Database content verification
3. Agent question-answering with document-specific queries
"""

import asyncio
from src.dependencies import AgentDependencies
from src.agent import rag_agent, RAGState
from pydantic_ai.ag_ui import StateDeps


# Document-specific test questions based on actual content
TEST_QUESTIONS = [
    {
        "question": "When was NeuralFlow AI founded and how many employees do they have?",
        "expected_content": ["2023", "47 employees"],
        "expected_doc": "company-overview",
        "category": "Factual - Company Info"
    },
    {
        "question": "What is NeuralFlow AI's revenue goal for 2025?",
        "expected_content": ["$12M", "12M", "annual recurring revenue"],
        "expected_doc": "mission-and-goals",
        "category": "Factual - Financial Goals"
    },
    {
        "question": "What are the three flagship AI automation products NeuralFlow plans to launch, and in which quarters?",
        "expected_content": ["DocFlow AI", "ConversePro", "AnalyticsMind", "Q2", "Q3", "Q4"],
        "expected_doc": "mission-and-goals",
        "category": "Factual - Product Roadmap"
    },
    {
        "question": "What is the work arrangement at NeuralFlow AI - which days are in office and which are remote?",
        "expected_content": ["Tuesday", "Wednesday", "Thursday", "in office", "Monday", "Friday", "remote"],
        "expected_doc": "team-handbook",
        "category": "Factual - Work Policy"
    },
    {
        "question": "How much is the annual learning budget for each employee?",
        "expected_content": ["$2,500", "2500", "learning budget"],
        "expected_doc": "team-handbook",
        "category": "Factual - Benefits"
    },
    {
        "question": "What are the 5 phases of NeuralFlow's client implementation playbook?",
        "expected_content": ["Discovery", "Design", "Development", "Deployment", "Optimization"],
        "expected_doc": "implementation-playbook",
        "category": "Process - Implementation"
    },
    {
        "question": "What is NeuralFlow AI's client retention rate?",
        "expected_content": ["94%", "retention"],
        "expected_doc": "company-overview",
        "category": "Factual - Business Metrics"
    },
    {
        "question": "What are the core collaboration hours at NeuralFlow AI?",
        "expected_content": ["10 AM", "4 PM", "Pacific Time"],
        "expected_doc": "team-handbook",
        "category": "Factual - Work Policy"
    },
    {
        "question": "What industries does NeuralFlow AI specialize in?",
        "expected_content": ["financial", "healthcare", "legal", "e-commerce", "retail"],
        "expected_doc": "company-overview",
        "category": "Conceptual - Business Focus"
    },
    {
        "question": "How does NeuralFlow AI approach AI implementation according to their philosophy?",
        "expected_content": ["Start Small", "Scale Fast", "Co-Creation", "Continuous Iteration"],
        "expected_doc": "implementation-playbook",
        "category": "Conceptual - Philosophy"
    }
]


async def validate_database_deeply():
    """Deep validation of database content after ingestion."""
    print("\n" + "="*80)
    print("DEEP DATABASE VALIDATION")
    print("="*80)

    deps = AgentDependencies()
    await deps.initialize()

    # Count documents and chunks
    doc_count = await deps.db.documents.count_documents({})
    chunk_count = await deps.db.chunks.count_documents({})

    print(f"\n[1] Document Count: {doc_count}")
    print(f"[2] Chunk Count: {chunk_count}")

    # Verify expected document count (13 files)
    expected_docs = 13
    if doc_count != expected_docs:
        print(f"    [WARN]  WARNING: Expected {expected_docs} documents, found {doc_count}")
    else:
        print(f"    [OK] Document count matches expected ({expected_docs})")

    # Get all documents
    print("\n[3] Analyzing Documents:")
    cursor = deps.db.documents.find({})
    documents = [doc async for doc in cursor]

    for doc in documents:
        doc_chunks = await deps.db.chunks.count_documents({"document_id": doc["_id"]})
        content_len = doc.get('content_length', len(doc.get('content', '')))
        print(f"    - {doc.get('title', 'Untitled')[:50]:50s} | {doc_chunks:3d} chunks | {content_len:6d} chars")

    # Verify all chunks have embeddings
    print("\n[4] Verifying Chunk Quality:")
    cursor = deps.db.chunks.find({}).limit(5)
    sample_chunks = [chunk async for chunk in cursor]

    issues = []
    for i, chunk in enumerate(sample_chunks, 1):
        has_embedding = "embedding" in chunk and chunk["embedding"]
        has_content = "content" in chunk and len(chunk["content"]) > 0
        has_doc_id = "document_id" in chunk

        if not has_embedding:
            issues.append(f"Chunk {i}: Missing embedding")
        if not has_content:
            issues.append(f"Chunk {i}: Empty content")
        if not has_doc_id:
            issues.append(f"Chunk {i}: Missing document_id")

        if has_embedding:
            emb_len = len(chunk["embedding"])
            print(f"    Chunk {i}: {len(chunk['content']):4d} chars, embedding dim={emb_len}")

    if issues:
        print("\n    [WARN]  Issues found:")
        for issue in issues:
            print(f"       - {issue}")
    else:
        print("    [OK] All sampled chunks are valid")

    # Check for chunks without embeddings
    chunks_no_embedding = await deps.db.chunks.count_documents({"embedding": {"$exists": False}})
    chunks_empty_content = await deps.db.chunks.count_documents({"content": ""})

    print(f"\n[5] Data Quality Checks:")
    print(f"    Chunks missing embeddings: {chunks_no_embedding}")
    print(f"    Chunks with empty content: {chunks_empty_content}")

    if chunks_no_embedding > 0 or chunks_empty_content > 0:
        print("    [WARN]  WARNING: Data quality issues detected")
    else:
        print("    [OK] All chunks have embeddings and content")

    # Verify document-chunk relationships
    print("\n[6] Verifying Relationships:")
    orphan_chunks = 0
    for doc in documents:
        doc_chunks = await deps.db.chunks.count_documents({"document_id": doc["_id"]})
        if doc_chunks == 0:
            orphan_chunks += 1
            print(f"    [WARN]  Document '{doc['title']}' has no chunks!")

    if orphan_chunks == 0:
        print("    [OK] All documents have associated chunks")
    else:
        print(f"    [WARN]  {orphan_chunks} documents have no chunks")

    await deps.cleanup()

    print("\n" + "="*80)
    print("DATABASE VALIDATION COMPLETE")
    print("="*80)

    return {
        "doc_count": doc_count,
        "chunk_count": chunk_count,
        "issues": len(issues) + chunks_no_embedding + chunks_empty_content + orphan_chunks
    }


async def test_agent_question(question_data: dict, agent_state, agent_deps):
    """Test agent with a specific question."""
    print(f"\n{'='*80}")
    print(f"Question: {question_data['question']}")
    print(f"Category: {question_data['category']}")
    print(f"Expected Document: {question_data['expected_doc']}")
    print(f"{'='*80}")

    response_text = ""
    tool_called = False
    retrieved_content = ""

    # Run agent
    async with rag_agent.iter(
        question_data['question'],
        deps=agent_state,
        message_history=[]
    ) as run:
        async for node in run:
            # Check for tool calls
            if rag_agent.is_call_tools_node(node):
                tool_called = True
                async with node.stream(run.ctx) as tool_stream:
                    async for event in tool_stream:
                        event_type = type(event).__name__
                        if event_type == "FunctionToolResultEvent":
                            if hasattr(event, 'result'):
                                retrieved_content = str(event.result)[:500]

            # Collect response
            elif rag_agent.is_model_request_node(node):
                async with node.stream(run.ctx) as request_stream:
                    async for event in request_stream:
                        from pydantic_ai.messages import PartStartEvent, PartDeltaEvent, TextPartDelta
                        if isinstance(event, PartStartEvent) and event.part.part_kind == 'text':
                            if event.part.content:
                                response_text += event.part.content
                        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                            if event.delta.content_delta:
                                response_text += event.delta.content_delta

    # Analyze results
    print(f"\n[RESULTS]")
    print(f"Tool Called: {tool_called}")
    print(f"Response Length: {len(response_text)} characters")

    # Check if expected content appears in response
    found_keywords = []
    missing_keywords = []
    for keyword in question_data['expected_content']:
        if keyword.lower() in response_text.lower():
            found_keywords.append(keyword)
        else:
            missing_keywords.append(keyword)

    print(f"\nKeyword Analysis:")
    print(f"  Found ({len(found_keywords)}/{len(question_data['expected_content'])}): {found_keywords}")
    if missing_keywords:
        print(f"  Missing: {missing_keywords}")

    # Print response preview
    print(f"\nResponse Preview:")
    print(f"  {response_text[:300]}...")

    # Determine success
    success = tool_called and len(found_keywords) >= len(question_data['expected_content']) // 2

    if success:
        print(f"\n[OK] TEST PASSED")
    else:
        print(f"\n[FAIL] TEST FAILED")
        if not tool_called:
            print(f"  Reason: Tool was not called")
        else:
            print(f"  Reason: Missing expected content")

    return {
        "question": question_data['question'],
        "category": question_data['category'],
        "success": success,
        "tool_called": tool_called,
        "keywords_found": len(found_keywords),
        "keywords_expected": len(question_data['expected_content']),
        "response_length": len(response_text)
    }


async def main():
    """Run comprehensive end-to-end tests."""
    print("="*80)
    print("COMPREHENSIVE END-TO-END TESTING")
    print("RecallHub - Document-Specific Validation")
    print("="*80)

    # Step 1: Deep database validation
    print("\nSTEP 1: Validating ingested data...")
    db_results = await validate_database_deeply()

    if db_results['issues'] > 0:
        print(f"\n[WARN]  WARNING: {db_results['issues']} database issues detected!")
        print("Consider re-running ingestion if issues are critical.")

    # Step 2: Test agent with document-specific questions
    print("\n\nSTEP 2: Testing Agent with Document-Specific Questions...")
    print("="*80)

    # Create agent state
    state = RAGState()
    deps = StateDeps[RAGState](state=state)

    results = []
    for i, question_data in enumerate(TEST_QUESTIONS, 1):
        print(f"\n\n[Test {i}/{len(TEST_QUESTIONS)}]")
        try:
            result = await test_agent_question(question_data, deps, None)
            results.append(result)
        except Exception as e:
            print(f"\n[FAIL] TEST ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "question": question_data['question'],
                "category": question_data['category'],
                "success": False,
                "error": str(e)
            })

    # Final Summary
    print("\n\n" + "="*80)
    print("FINAL TEST SUMMARY")
    print("="*80)

    print(f"\nDatabase Validation:")
    print(f"  Documents: {db_results['doc_count']}")
    print(f"  Chunks: {db_results['chunk_count']}")
    print(f"  Issues: {db_results['issues']}")

    passed = sum(1 for r in results if r.get('success', False))
    total = len(results)

    print(f"\nAgent Tests:")
    print(f"  Total: {total}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {total - passed}")
    print(f"  Success Rate: {passed/total*100:.1f}%")

    print(f"\nResults by Category:")
    by_category = {}
    for r in results:
        cat = r.get('category', 'Unknown')
        if cat not in by_category:
            by_category[cat] = {'passed': 0, 'total': 0}
        by_category[cat]['total'] += 1
        if r.get('success', False):
            by_category[cat]['passed'] += 1

    for cat, stats in sorted(by_category.items()):
        print(f"  {cat:30s}: {stats['passed']}/{stats['total']}")

    print("\n" + "="*80)
    if passed == total and db_results['issues'] == 0:
        print("[SUCCESS] ALL TESTS PASSED! RAG system is working perfectly.")
    elif passed >= total * 0.8:
        print("[OK] Most tests passed. System is functional with minor issues.")
    else:
        print("[WARN]  Multiple test failures. Review results above.")
    print("="*80)

    return 0 if passed == total else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
