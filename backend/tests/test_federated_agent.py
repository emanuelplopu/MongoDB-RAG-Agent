"""Unit tests for the Federated Agent system."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agent.schemas import (
    DataSource, DataSourceType, AccessType,
    TaskDefinition, TaskType, AgentPlan, EvaluationDecision,
    DocumentReference, WebReference, WorkerResult, ResultQuality,
    OrchestratorStep, OrchestratorPhase, WorkerStep, AgentTrace,
    AgentModeConfig, AgentMode
)


class TestAgentSchemas:
    """Tests for agent schema models."""
    
    def test_data_source_creation(self):
        """Test creating a DataSource."""
        source = DataSource(
            id="profile_test",
            type=DataSourceType.PROFILE,
            database="rag_test",
            access_type=AccessType.PROFILE,
            profile_key="test",
            display_name="Test Profile"
        )
        
        assert source.id == "profile_test"
        assert source.type == DataSourceType.PROFILE
        assert source.database == "rag_test"
        assert source.collection_chunks == "chunks"  # default
        assert source.collection_documents == "documents"  # default
    
    def test_task_definition(self):
        """Test creating a TaskDefinition."""
        task = TaskDefinition(
            type=TaskType.SEARCH_PROFILE,
            query="test query",
            max_results=5
        )
        
        assert task.type == TaskType.SEARCH_PROFILE
        assert task.query == "test query"
        assert task.max_results == 5
        assert task.priority == 1  # default
        assert len(task.id) == 8  # auto-generated ID
    
    def test_agent_plan_get_ready_tasks(self):
        """Test AgentPlan.get_ready_tasks with dependencies."""
        task1 = TaskDefinition(id="t1", type=TaskType.SEARCH_ALL, query="q1")
        task2 = TaskDefinition(id="t2", type=TaskType.SEARCH_ALL, query="q2", depends_on=["t1"])
        task3 = TaskDefinition(id="t3", type=TaskType.SEARCH_ALL, query="q3")
        
        plan = AgentPlan(
            intent_summary="test",
            reasoning="test",
            tasks=[task1, task2, task3],
            success_criteria="test"
        )
        
        # Initially, t1 and t3 should be ready (no dependencies)
        ready = plan.get_ready_tasks(set())
        assert len(ready) == 2
        assert task1 in ready
        assert task3 in ready
        assert task2 not in ready
        
        # After t1 completes, t2 should also be ready
        ready = plan.get_ready_tasks({"t1"})
        assert len(ready) == 3
        assert task2 in ready
    
    def test_document_reference(self):
        """Test DocumentReference creation."""
        doc = DocumentReference(
            id="chunk123",
            document_id="doc456",
            title="Test Document",
            source_type=DataSourceType.PROFILE,
            source_database="rag_test",
            excerpt="This is a test excerpt...",
            similarity_score=0.85
        )
        
        assert doc.id == "chunk123"
        assert doc.similarity_score == 0.85
        assert doc.source_type == DataSourceType.PROFILE
    
    def test_worker_result_total_results(self):
        """Test WorkerResult.total_results property."""
        result = WorkerResult(
            task_id="t1",
            task_type=TaskType.SEARCH_ALL,
            query="test",
            documents_found=[
                DocumentReference(
                    id="1", document_id="d1", title="Doc 1",
                    source_type=DataSourceType.PROFILE,
                    source_database="db", excerpt="..."
                ),
                DocumentReference(
                    id="2", document_id="d2", title="Doc 2",
                    source_type=DataSourceType.PROFILE,
                    source_database="db", excerpt="..."
                )
            ],
            web_links_found=[
                WebReference(url="https://example.com", title="Example")
            ]
        )
        
        assert result.total_results == 3
    
    def test_agent_trace_add_steps(self):
        """Test AgentTrace step aggregation."""
        trace = AgentTrace(
            orchestrator_model="gpt-4o",
            worker_model="gemini-flash",
            mode=AgentMode.THINKING
        )
        
        # Add orchestrator step
        orch_step = OrchestratorStep(
            phase=OrchestratorPhase.ANALYZE,
            model="gpt-4o",
            input_summary="test",
            reasoning="reasoning",
            output_summary="output",
            tokens_used=100,
            duration_ms=500.0
        )
        trace.add_orchestrator_step(orch_step)
        
        assert len(trace.orchestrator_steps) == 1
        assert trace.orchestrator_tokens == 100
        assert trace.total_tokens == 100
        
        # Add worker step with document
        worker_step = WorkerStep(
            task_id="t1",
            task_type=TaskType.SEARCH_ALL,
            model="gemini-flash",
            tool_name="search_all",
            tool_input={"query": "test"},
            documents=[
                DocumentReference(
                    id="doc1", document_id="d1", title="Test",
                    source_type=DataSourceType.PROFILE,
                    source_database="db", excerpt="..."
                )
            ],
            tokens_used=50,
            duration_ms=200.0
        )
        trace.add_worker_step(worker_step)
        
        assert len(trace.worker_steps) == 1
        assert trace.worker_tokens == 50
        assert trace.total_tokens == 150
        assert len(trace.all_documents) == 1
    
    def test_agent_mode_config_should_use_thinking(self):
        """Test AgentModeConfig.should_use_thinking heuristics."""
        config = AgentModeConfig(mode=AgentMode.AUTO)
        
        # Simple query - should not use thinking
        assert not config.should_use_thinking("hi")
        
        # Complex query - should use thinking
        assert config.should_use_thinking("explain how the accounting system works step by step")
        
        # Forced thinking mode
        config.mode = AgentMode.THINKING
        assert config.should_use_thinking("hi")
        
        # Forced fast mode
        config.mode = AgentMode.FAST
        assert not config.should_use_thinking("explain everything in detail step by step")
    
    def test_evaluation_decision(self):
        """Test EvaluationDecision model."""
        decision = EvaluationDecision(
            findings_summary="Found relevant documents",
            decision="sufficient",
            reasoning="Enough information to answer",
            confidence=0.9
        )
        
        assert decision.decision == "sufficient"
        assert decision.confidence == 0.9
        assert len(decision.follow_up_tasks) == 0


class TestFederatedSearch:
    """Tests for FederatedSearch."""
    
    def test_get_accessible_sources(self):
        """Test getting accessible sources for a user."""
        from backend.agent.federated_search import FederatedSearch
        
        fs = FederatedSearch()
        
        sources = fs.get_accessible_sources(
            user_id="user123",
            user_email="john.doe@example.com",
            active_profile_key="company",
            active_profile_database="rag_company"
        )
        
        # Should have at least profile, personal, cloud_private, cloud_shared
        assert len(sources) >= 3
        
        # Check profile source
        profile_sources = [s for s in sources if s.type == DataSourceType.PROFILE]
        assert len(profile_sources) == 1
        assert profile_sources[0].database == "rag_company"
        
        # Check personal source
        personal_sources = [s for s in sources if s.type == DataSourceType.PERSONAL]
        assert len(personal_sources) == 1
        assert "john_doe" in personal_sources[0].database
    
    def test_assess_result_quality(self):
        """Test result quality assessment."""
        from backend.agent.federated_search import FederatedSearch
        
        fs = FederatedSearch()
        
        # Empty results
        assert fs.assess_result_quality([]) == ResultQuality.EMPTY
        
        # Create some document references
        docs = [
            DocumentReference(
                id=f"doc{i}",
                document_id=f"d{i}",
                title=f"Doc {i}",
                source_type=DataSourceType.PROFILE,
                source_database="db",
                excerpt="...",
                similarity_score=0.9
            )
            for i in range(5)
        ]
        
        # 5 docs with high score = EXCELLENT
        assert fs.assess_result_quality(docs) == ResultQuality.EXCELLENT
        
        # 1 doc = PARTIAL
        assert fs.assess_result_quality(docs[:1]) == ResultQuality.PARTIAL
    
    def test_deduplicate_and_rank(self):
        """Test deduplication and ranking."""
        from backend.agent.federated_search import FederatedSearch
        
        fs = FederatedSearch()
        
        # Create duplicate documents
        docs = [
            DocumentReference(
                id="doc1", document_id="d1", title="Doc 1",
                source_type=DataSourceType.PROFILE, source_database="db",
                excerpt="...", similarity_score=0.9
            ),
            DocumentReference(
                id="doc1", document_id="d1", title="Doc 1",  # duplicate
                source_type=DataSourceType.CLOUD_SHARED, source_database="cloud",
                excerpt="...", similarity_score=0.8
            ),
            DocumentReference(
                id="doc2", document_id="d2", title="Doc 2",
                source_type=DataSourceType.PROFILE, source_database="db",
                excerpt="...", similarity_score=0.95
            ),
        ]
        
        result = fs._deduplicate_and_rank(docs, 10)
        
        # Should have 2 unique docs
        assert len(result) == 2
        
        # Should be sorted by score (doc2 first)
        assert result[0].id == "doc2"
        assert result[1].id == "doc1"


class TestWorkerPool:
    """Tests for WorkerPool."""
    
    @pytest.mark.asyncio
    async def test_assess_quality(self):
        """Test result quality assessment."""
        from backend.agent.worker_pool import WorkerPool
        
        pool = WorkerPool()
        
        # No results
        quality = pool._assess_quality([], [])
        assert quality == ResultQuality.EMPTY
        
        # Some documents
        docs = [
            DocumentReference(
                id="doc1", document_id="d1", title="Test",
                source_type=DataSourceType.PROFILE, source_database="db",
                excerpt="...", similarity_score=0.9
            )
        ]
        quality = pool._assess_quality(docs, [])
        assert quality == ResultQuality.PARTIAL
    
    def test_suggest_refinements(self):
        """Test query refinement suggestions."""
        from backend.agent.worker_pool import WorkerPool
        
        pool = WorkerPool()
        
        task = TaskDefinition(
            type=TaskType.SEARCH_PROFILE,
            query="company document"
        )
        
        suggestions = pool._suggest_refinements(task, [], [], ResultQuality.EMPTY)
        
        # Should suggest some refinements
        assert len(suggestions) > 0
    
    def test_get_tool_name(self):
        """Test tool name mapping."""
        from backend.agent.worker_pool import WorkerPool
        
        pool = WorkerPool()
        
        assert pool._get_tool_name(TaskType.SEARCH_PROFILE) == "search_profile_documents"
        assert pool._get_tool_name(TaskType.WEB_SEARCH) == "web_search"
        assert pool._get_tool_name(TaskType.BROWSE_WEB) == "browse_web"


class TestOrchestrator:
    """Tests for Orchestrator."""
    
    def test_reset(self):
        """Test orchestrator reset."""
        from backend.agent.orchestrator import Orchestrator
        
        orch = Orchestrator(model="gpt-4o")
        
        # Add a dummy step
        orch.steps.append(OrchestratorStep(
            phase=OrchestratorPhase.ANALYZE,
            model="gpt-4o",
            input_summary="test",
            reasoning="test",
            output_summary="test"
        ))
        
        assert len(orch.steps) == 1
        
        orch.reset()
        
        assert len(orch.steps) == 0


class TestFederatedAgent:
    """Tests for FederatedAgent coordinator."""
    
    def test_create_trace(self):
        """Test trace creation."""
        from backend.agent.coordinator import FederatedAgent
        
        config = AgentModeConfig(
            mode=AgentMode.THINKING,
            orchestrator_model="gpt-4o",
            worker_model="gemini-flash"
        )
        
        agent = FederatedAgent(config=config)
        trace = agent._create_trace(user_id="user1", session_id="session1")
        
        assert trace.orchestrator_model == "gpt-4o"
        assert trace.worker_model == "gemini-flash"
        assert trace.mode == AgentMode.THINKING
        assert trace.user_id == "user1"
        assert trace.session_id == "session1"
    
    def test_factory_function(self):
        """Test create_federated_agent factory."""
        from backend.agent.coordinator import create_federated_agent
        
        agent = create_federated_agent(
            mode=AgentMode.FAST,
            max_iterations=5
        )
        
        assert agent.config.mode == AgentMode.FAST
        assert agent.config.max_iterations == 5


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
