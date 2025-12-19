"""Integration tests for the Federated Agent system.

These tests require:
- MongoDB running (can use docker-compose)
- LLM API keys configured

Run with: pytest backend/tests/integration/test_federated_agent_integration.py -v
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from backend.agent.schemas import (
    AgentMode, AgentModeConfig, TaskType, TaskDefinition,
    DataSourceType, DocumentReference, ResultQuality
)
from backend.agent.federated_search import FederatedSearch, get_federated_search
from backend.agent.worker_pool import WorkerPool
from backend.agent.orchestrator import Orchestrator
from backend.agent.coordinator import FederatedAgent, create_federated_agent


class TestFederatedSearchIntegration:
    """Integration tests for FederatedSearch."""
    
    @pytest.mark.asyncio
    async def test_get_embedding(self):
        """Test embedding generation."""
        fs = FederatedSearch()
        
        # Mock the embedding client
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        
        # Set the embedding client directly
        fs._embedding_client = mock_client
        
        embedding = await fs.get_embedding("test query")
        
        assert len(embedding) == 1536
        assert embedding[0] == 0.1
    
    @pytest.mark.asyncio
    async def test_search_with_mocked_database(self):
        """Test federated search with mocked database."""
        fs = FederatedSearch()
        
        # Mock the client and embedding
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_collection.count_documents = AsyncMock(return_value=10)
        
        # Mock aggregate cursor
        async def mock_aggregate(pipeline):
            class MockCursor:
                def __init__(self):
                    self.docs = [
                        {
                            "chunk_id": "chunk1",
                            "document_id": "doc1",
                            "content": "Test content about company",
                            "similarity": 0.9,
                            "metadata": {},
                            "document_title": "Company Doc",
                            "document_source": "test.pdf"
                        }
                    ]
                    self.idx = 0
                
                def __aiter__(self):
                    return self
                
                async def __anext__(self):
                    if self.idx >= len(self.docs):
                        raise StopAsyncIteration
                    doc = self.docs[self.idx]
                    self.idx += 1
                    return doc
            
            return MockCursor()
        
        mock_collection.aggregate = mock_aggregate
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)
        
        # Mock the client
        mock_client = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_db)
        fs._client = mock_client
        
        # Mock embedding
        fs.get_embedding = AsyncMock(return_value=[0.1] * 1536)
        
        # Run search
        results, metadata = await fs.search(
            query="company information",
            user_id="user1",
            user_email="test@example.com",
            active_profile_key="test",
            active_profile_database="rag_test"
        )
        
        # Verify results
        assert metadata["sources_searched"] > 0


class TestWorkerPoolIntegration:
    """Integration tests for WorkerPool."""
    
    @pytest.mark.asyncio
    async def test_execute_search_task(self):
        """Test executing a search task."""
        # Create worker pool with mocked search
        mock_search = AsyncMock()
        mock_search.search = AsyncMock(return_value=(
            [
                DocumentReference(
                    id="doc1",
                    document_id="d1",
                    title="Test Doc",
                    source_type=DataSourceType.PROFILE,
                    source_database="test_db",
                    excerpt="Test content",
                    similarity_score=0.85
                )
            ],
            {"sources_searched": 1, "sources_with_results": 1}
        ))
        
        pool = WorkerPool(federated_search=mock_search)
        
        # Create task
        task = TaskDefinition(
            id="task1",
            type=TaskType.SEARCH_ALL,
            query="test query",
            max_results=10
        )
        
        # Execute
        results = await pool.execute_tasks(
            tasks=[task],
            user_id="user1",
            user_email="test@example.com",
            active_profile_key="test",
            active_profile_database="test_db"
        )
        
        # Verify
        assert len(results) == 1
        assert results[0].task_id == "task1"
        assert results[0].success
        assert len(results[0].documents_found) == 1
        assert results[0].documents_found[0].title == "Test Doc"
    
    @pytest.mark.asyncio
    async def test_execute_parallel_tasks(self):
        """Test executing multiple tasks in parallel."""
        # Create mock search that tracks call order
        call_order = []
        
        async def mock_search(*args, **kwargs):
            call_order.append(datetime.now())
            await asyncio.sleep(0.1)  # Simulate some work
            return ([], {"sources_searched": 1})
        
        mock_federated = AsyncMock()
        mock_federated.search = mock_search
        
        pool = WorkerPool(federated_search=mock_federated, max_workers=4)
        
        # Create multiple tasks
        tasks = [
            TaskDefinition(id=f"task{i}", type=TaskType.SEARCH_ALL, query=f"query{i}")
            for i in range(4)
        ]
        
        start = datetime.now()
        results = await pool.execute_tasks(
            tasks=tasks,
            user_id="user1",
            user_email="test@example.com"
        )
        duration = (datetime.now() - start).total_seconds()
        
        # All 4 tasks should complete
        assert len(results) == 4
        
        # If tasks ran in parallel, total time should be ~0.1s not ~0.4s
        # Allow some margin for overhead
        assert duration < 0.3, f"Tasks should run in parallel, took {duration}s"
    
    @pytest.mark.asyncio
    async def test_task_dependencies(self):
        """Test that task dependencies are respected."""
        execution_order = []
        
        async def mock_search(*args, **kwargs):
            # Track which task is executing based on query
            query = kwargs.get("query", args[0] if args else "unknown")
            execution_order.append(query)
            return ([], {"sources_searched": 1})
        
        mock_federated = AsyncMock()
        mock_federated.search = mock_search
        
        pool = WorkerPool(federated_search=mock_federated, max_workers=4)
        
        # Create tasks with dependencies
        task1 = TaskDefinition(id="task1", type=TaskType.SEARCH_ALL, query="first_query")
        task2 = TaskDefinition(id="task2", type=TaskType.SEARCH_ALL, query="second_query", depends_on=["task1"])
        
        results = await pool.execute_tasks(
            tasks=[task1, task2],
            user_id="user1",
            user_email="test@example.com"
        )
        
        assert len(results) == 2
        # task1 should execute before task2
        assert execution_order.index("first_query") < execution_order.index("second_query")


class TestOrchestratorIntegration:
    """Integration tests for Orchestrator."""
    
    @pytest.mark.asyncio
    async def test_analyze_phase(self):
        """Test the analyze phase with mocked LLM."""
        orch = Orchestrator(model="gpt-4o")
        
        # Mock the LLM call
        mock_response = {
            "intent_summary": "User wants company information",
            "key_entities": ["company", "information"],
            "sources_needed": ["profile"],
            "is_complex": False,
            "requires_multiple_searches": False,
            "reasoning": "Simple query about company"
        }
        
        with patch.object(orch, '_call_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            
            result = await orch.analyze(
                user_message="Tell me about the company",
                conversation_history=[]
            )
            
            assert result["intent_summary"] == "User wants company information"
            assert "profile" in result["sources_needed"]
    
    @pytest.mark.asyncio
    async def test_plan_phase(self):
        """Test the plan phase."""
        orch = Orchestrator(model="gpt-4o")
        
        mock_response = {
            "intent_summary": "Find company information",
            "reasoning": "Search profile documents first",
            "strategy": "parallel",
            "tasks": [
                {
                    "id": "t1",
                    "type": "search_profile",
                    "query": "company overview",
                    "sources": [],
                    "priority": 1,
                    "depends_on": [],
                    "max_results": 10
                }
            ],
            "success_criteria": "Find company overview document",
            "max_iterations": 3
        }
        
        with patch.object(orch, '_call_llm', new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_response
            
            analysis = {
                "intent_summary": "Company information",
                "sources_needed": ["profile"]
            }
            
            plan = await orch.plan(
                analysis=analysis,
                available_sources=[{"id": "profile_test", "type": "profile", "display_name": "Test"}]
            )
            
            assert plan.strategy == "parallel"
            assert len(plan.tasks) == 1
            assert plan.tasks[0].type == TaskType.SEARCH_PROFILE


class TestFederatedAgentIntegration:
    """Integration tests for the complete FederatedAgent flow."""
    
    @pytest.mark.asyncio
    async def test_fast_mode_flow(self):
        """Test the fast mode (no orchestration)."""
        config = AgentModeConfig(
            mode=AgentMode.FAST,
            orchestrator_model="gpt-4o",
            worker_model="gemini-flash"
        )
        
        agent = FederatedAgent(config=config)
        
        # Mock worker pool
        mock_result = [
            MagicMock(
                task_id="task1",
                documents_found=[
                    DocumentReference(
                        id="doc1", document_id="d1", title="Test Doc",
                        source_type=DataSourceType.PROFILE, source_database="db",
                        excerpt="Test content", full_content="Full test content",
                        similarity_score=0.9
                    )
                ],
                web_links_found=[]
            )
        ]
        agent.worker_pool.execute_tasks = AsyncMock(return_value=mock_result)
        
        # Mock LLM for response generation - patch the method directly instead of litellm
        agent._generate_fast_response = AsyncMock(return_value="Based on the documents, here is the answer.")
        
        response, trace = await agent.process(
            user_message="What is the company about?",
            user_id="user1",
            user_email="test@example.com",
            active_profile_key="test",
            active_profile_database="test_db"
        )
        
        assert "answer" in response.lower() or "documents" in response.lower()
        assert trace.mode == AgentMode.FAST
        assert trace.iterations == 1
    
    @pytest.mark.asyncio
    async def test_thinking_mode_flow(self):
        """Test the thinking mode with full orchestration."""
        config = AgentModeConfig(
            mode=AgentMode.THINKING,
            orchestrator_model="gpt-4o",
            worker_model="gemini-flash",
            max_iterations=2
        )
        
        agent = FederatedAgent(config=config)
        
        # Mock orchestrator methods
        agent.orchestrator.analyze = AsyncMock(return_value={
            "intent_summary": "Find company info",
            "sources_needed": ["profile"]
        })
        
        from backend.agent.schemas import AgentPlan
        mock_plan = AgentPlan(
            intent_summary="Find company info",
            reasoning="Search profile",
            tasks=[
                TaskDefinition(id="t1", type=TaskType.SEARCH_PROFILE, query="company")
            ],
            success_criteria="Find company doc",
            max_iterations=2
        )
        agent.orchestrator.plan = AsyncMock(return_value=mock_plan)
        
        mock_eval = EvaluationDecision(
            findings_summary="Found relevant documents",
            decision="sufficient",
            reasoning="Good results",
            confidence=0.9
        )
        agent.orchestrator.evaluate = AsyncMock(return_value=mock_eval)
        agent.orchestrator.synthesize = AsyncMock(return_value="Here is the answer about the company.")
        
        # Mock worker pool
        from backend.agent.schemas import WorkerResult
        mock_worker_result = WorkerResult(
            task_id="t1",
            task_type=TaskType.SEARCH_PROFILE,
            query="company",
            documents_found=[
                DocumentReference(
                    id="doc1", document_id="d1", title="Company Overview",
                    source_type=DataSourceType.PROFILE, source_database="db",
                    excerpt="Company info...", similarity_score=0.9
                )
            ],
            result_quality=ResultQuality.GOOD
        )
        agent.worker_pool.execute_tasks = AsyncMock(return_value=[mock_worker_result])
        
        # Run
        response, trace = await agent.process(
            user_message="Tell me about the company",
            user_id="user1",
            user_email="test@example.com",
            active_profile_key="test",
            active_profile_database="test_db"
        )
        
        # Verify
        assert "company" in response.lower()
        assert trace.mode == AgentMode.THINKING
        assert len(trace.all_documents) == 1
        assert agent.orchestrator.analyze.called
        assert agent.orchestrator.plan.called
        assert agent.orchestrator.evaluate.called
        assert agent.orchestrator.synthesize.called


# Import EvaluationDecision for tests
from backend.agent.schemas import EvaluationDecision


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
