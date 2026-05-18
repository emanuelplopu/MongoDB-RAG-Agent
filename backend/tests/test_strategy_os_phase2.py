"""
Unit tests for Strategy OS Phase 2 (DAG Engine Components).

Tests verify:
- Graph compiler: compilation, validation, topological levels, cycle detection
- Condition evaluator: safe expression evaluation and security sandboxing
- Strategy runner: DAG execution, state merging, budget enforcement, error handling
- Node registry: registration, dispatch, timing, exception wrapping
- Individual nodes: NormalizeQuery, BoilerplateFilter, DedupeVersions, EvidenceCards,
  Synthesize, ValidateCitations, Refine
- Full DAG execution integration with mock and real nodes
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agent.strategy.models import (
    BusinessContext,
    CitationRef,
    EvidenceCard,
    NodeOutput,
    RetrievedChunk,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunState,
    StrategySpec,
    SynthesisResult,
    ValidationResult,
    ValidationIssue,
)
from backend.agent.strategy.graph_compiler import (
    CompiledGraph,
    GraphCompilationError,
    compile_graph,
    topological_levels,
)
from backend.agent.strategy.condition_evaluator import (
    ConditionEvaluationError,
    restricted_eval,
)
from backend.agent.strategy.strategy_runner import (
    StateViolationError,
    StrategyExecutionError,
    StrategyRunner,
)
from backend.agent.strategy.nodes.registry import (
    NodeRegistry,
    NodeRegistryError,
    create_default_registry,
)
from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def make_simple_graph() -> StrategyGraph:
    """3 nodes in linear sequence: n1 → n2 → n3."""
    return StrategyGraph(
        nodes=[
            StrategyNode(node_id="n1", node_type="normalize_query", config={}),
            StrategyNode(node_id="n2", node_type="retrieve", config={}),
            StrategyNode(node_id="n3", node_type="synthesize", config={}),
        ],
        edges=[
            StrategyEdge(from_node="n1", to_node="n2"),
            StrategyEdge(from_node="n2", to_node="n3"),
        ],
        entry_node="n1",
        terminal_nodes=["n3"],
    )


def make_parallel_graph() -> StrategyGraph:
    """entry → 2 parallel nodes → terminal."""
    return StrategyGraph(
        nodes=[
            StrategyNode(node_id="entry", node_type="normalize_query", config={}),
            StrategyNode(node_id="p1", node_type="retrieve", config={}),
            StrategyNode(node_id="p2", node_type="evidence_cards", config={}),
            StrategyNode(node_id="terminal", node_type="synthesize", config={}),
        ],
        edges=[
            StrategyEdge(from_node="entry", to_node="p1"),
            StrategyEdge(from_node="entry", to_node="p2"),
            StrategyEdge(from_node="p1", to_node="terminal"),
            StrategyEdge(from_node="p2", to_node="terminal"),
        ],
        entry_node="entry",
        terminal_nodes=["terminal"],
    )


def make_test_chunks(n: int = 3) -> list[RetrievedChunk]:
    """Create n realistic test chunks with enough content to pass boilerplate filter."""
    return [
        RetrievedChunk(
            chunk_id=f"chunk_{i}",
            document_id=f"doc_{i}",
            document_title=f"Document {i}",
            source_id=f"src_{i}",
            content=f"This is test content for chunk {i} with enough text to be meaningful and pass filters.",
            score=0.9 - (i * 0.1),
            search_type="semantic",
        )
        for i in range(n)
    ]


def make_test_spec(graph: StrategyGraph, budgets: StrategyBudgets | None = None) -> StrategySpec:
    """Build a StrategySpec from a graph."""
    return StrategySpec(
        strategy_id="test-strategy",
        graph=graph,
        budgets=budgets or StrategyBudgets(),
    )


def make_business_context() -> BusinessContext:
    """Create a minimal BusinessContext for tests."""
    return BusinessContext(
        capability_id="general_qa",
        tenant_id="test",
        profile_key="default",
    )


class MockNodeExecutor(NodeExecutor):
    """Mock executor that returns configurable outputs."""

    def __init__(self, output_data=None, status="success", delay=0):
        self.output_data = output_data
        self.status = status
        self.delay = delay
        self.call_count = 0

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        self.call_count += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status=self.status,
            output_data=self.output_data,
            tokens_used=0,
        )


class FailingNodeExecutor(NodeExecutor):
    """Mock executor that raises an exception."""

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        raise RuntimeError("Simulated executor failure")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Graph Compiler Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGraphCompiler:
    """Tests for compile_graph() and topological_levels()."""

    def test_compile_simple_linear_graph(self):
        """3 nodes in sequence produces 3 topological levels."""
        graph = make_simple_graph()
        compiled = compile_graph(graph)

        assert len(compiled.levels) == 3
        assert compiled.entry_node == "n1"
        assert compiled.terminal_nodes == ["n3"]
        assert "n1" in compiled.node_map
        assert "n2" in compiled.node_map
        assert "n3" in compiled.node_map

    def test_compile_parallel_graph(self):
        """entry → 2 parallel nodes → terminal produces correct levels."""
        graph = make_parallel_graph()
        compiled = compile_graph(graph)

        # Level 0: entry, Level 1: p1+p2, Level 2: terminal
        assert len(compiled.levels) == 3
        assert compiled.levels[0] == ["entry"]
        assert sorted(compiled.levels[1]) == ["p1", "p2"]
        assert compiled.levels[2] == ["terminal"]

    def test_compile_detects_cycle(self):
        """A→B→C→A raises GraphCompilationError with cycle info."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="a", node_type="normalize_query", config={}),
                StrategyNode(node_id="b", node_type="retrieve", config={}),
                StrategyNode(node_id="c", node_type="synthesize", config={}),
            ],
            edges=[
                StrategyEdge(from_node="a", to_node="b"),
                StrategyEdge(from_node="b", to_node="c"),
                StrategyEdge(from_node="c", to_node="a"),
            ],
            entry_node="a",
            terminal_nodes=["c"],
        )

        with pytest.raises(GraphCompilationError, match="[Cc]ycle"):
            compile_graph(graph)

    def test_compile_rejects_missing_entry_node(self):
        """entry_node not in nodes list raises GraphCompilationError."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={}),
            ],
            edges=[],
            entry_node="nonexistent",
            terminal_nodes=["n1"],
        )

        with pytest.raises(GraphCompilationError, match="Entry node"):
            compile_graph(graph)

    def test_compile_rejects_missing_edge_reference(self):
        """Edge references non-existent node raises GraphCompilationError."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={}),
                StrategyNode(node_id="n2", node_type="retrieve", config={}),
            ],
            edges=[
                StrategyEdge(from_node="n1", to_node="ghost"),
            ],
            entry_node="n1",
            terminal_nodes=["n2"],
        )

        with pytest.raises(GraphCompilationError, match="unknown"):
            compile_graph(graph)

    def test_compile_rejects_duplicate_node_ids(self):
        """Two nodes with same id raises GraphCompilationError."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={}),
                StrategyNode(node_id="n1", node_type="retrieve", config={}),
            ],
            edges=[],
            entry_node="n1",
            terminal_nodes=["n1"],
        )

        with pytest.raises(GraphCompilationError, match="[Dd]uplicate"):
            compile_graph(graph)

    def test_topological_levels_correct_ordering(self):
        """Verify level assignments match dependency ordering."""
        graph = make_simple_graph()
        levels = topological_levels(graph)

        # n1 has no predecessors → level 0
        # n2 depends on n1 → level 1
        # n3 depends on n2 → level 2
        assert levels[0] == ["n1"]
        assert levels[1] == ["n2"]
        assert levels[2] == ["n3"]

    def test_compile_with_conditional_edges(self):
        """Edges with conditions stored in compiled.conditional_edges."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={}),
                StrategyNode(node_id="n2", node_type="retrieve", config={}),
                StrategyNode(node_id="n3", node_type="synthesize", config={}),
            ],
            edges=[
                StrategyEdge(from_node="n1", to_node="n2", condition="chunks_count > 0"),
                StrategyEdge(from_node="n2", to_node="n3"),
            ],
            entry_node="n1",
            terminal_nodes=["n3"],
        )

        compiled = compile_graph(graph)
        assert "n1::n2" in compiled.conditional_edges
        assert compiled.conditional_edges["n1::n2"] == "chunks_count > 0"
        assert compiled.get_condition("n1", "n2") == "chunks_count > 0"
        assert compiled.get_condition("n2", "n3") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Condition Evaluator Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConditionEvaluator:
    """Tests for restricted_eval() security and correctness."""

    def test_simple_comparison(self):
        """restricted_eval('x > 5', {'x': 10}) → True."""
        assert restricted_eval("x > 5", {"x": 10}) is True
        assert restricted_eval("x > 5", {"x": 3}) is False

    def test_boolean_logic(self):
        """restricted_eval('x > 0 and y < 10', {'x': 5, 'y': 3}) → True."""
        assert restricted_eval("x > 0 and y < 10", {"x": 5, "y": 3}) is True
        assert restricted_eval("x > 0 and y < 10", {"x": -1, "y": 3}) is False

    def test_len_builtin(self):
        """restricted_eval('len(items) > 0', {'items': [1,2,3]}) → True."""
        assert restricted_eval("len(items) > 0", {"items": [1, 2, 3]}) is True
        assert restricted_eval("len(items) > 0", {"items": []}) is False

    def test_membership_in(self):
        """restricted_eval(\"'foo' in names\", {'names': ['foo','bar']}) → True."""
        assert restricted_eval("'foo' in names", {"names": ["foo", "bar"]}) is True
        assert restricted_eval("'baz' in names", {"names": ["foo", "bar"]}) is False

    def test_attribute_access(self):
        """restricted_eval('obj.value > 0', {'obj': ...}) → True."""
        obj = type("O", (), {"value": 5})()
        assert restricted_eval("obj.value > 0", {"obj": obj}) is True

    def test_blocks_import(self):
        """restricted_eval(\"__import__('os')\", {}) raises ConditionEvaluationError."""
        with pytest.raises(ConditionEvaluationError):
            restricted_eval("__import__('os')", {})

    def test_blocks_dunder_access(self):
        """restricted_eval('obj.__class__', {'obj': {}}) raises ConditionEvaluationError."""
        with pytest.raises(ConditionEvaluationError):
            restricted_eval("obj.__class__", {"obj": {}})

    def test_blocks_private_attr(self):
        """restricted_eval('obj._secret', ...) raises ConditionEvaluationError."""
        obj = type("O", (), {"_secret": 1})()
        with pytest.raises(ConditionEvaluationError):
            restricted_eval("obj._secret", {"obj": obj})


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Strategy Runner Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyRunner:
    """Tests for StrategyRunner DAG execution."""

    def _make_registry_with_mocks(self, node_types: dict[str, MockNodeExecutor]) -> NodeRegistry:
        """Build a registry with mock executors."""
        registry = NodeRegistry()
        for ntype, executor_cls in node_types.items():
            # Register the executor class wrapper
            registry._executors[ntype] = type(executor_cls)
            registry._instances[ntype] = executor_cls
        return registry

    @pytest.mark.asyncio
    async def test_run_single_node_graph(self):
        """One-node graph executes successfully."""
        graph = StrategyGraph(
            nodes=[StrategyNode(node_id="only", node_type="normalize_query", config={})],
            edges=[],
            entry_node="only",
            terminal_nodes=["only"],
        )
        spec = make_test_spec(graph)
        mock_exec = MockNodeExecutor(output_data="normalized query")
        registry = self._make_registry_with_mocks({"normalize_query": mock_exec})
        runner = StrategyRunner(registry=registry)

        result = await runner.run(spec, make_business_context(), query="hello")

        assert mock_exec.call_count == 1
        assert result.state.node_outputs["only"].status == "success"

    @pytest.mark.asyncio
    async def test_run_linear_three_node_graph(self):
        """3 nodes in sequence all execute in order."""
        graph = make_simple_graph()
        spec = make_test_spec(graph)

        mock_n = MockNodeExecutor(output_data="normalized")
        mock_r = MockNodeExecutor(output_data=[])
        mock_s = MockNodeExecutor(output_data={"text": "result", "citations": []})

        registry = self._make_registry_with_mocks({
            "normalize_query": mock_n,
            "retrieve": mock_r,
            "synthesize": mock_s,
        })
        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test")

        assert mock_n.call_count == 1
        assert mock_r.call_count == 1
        assert mock_s.call_count == 1
        assert "n1" in result.state.node_outputs
        assert "n2" in result.state.node_outputs
        assert "n3" in result.state.node_outputs

    @pytest.mark.asyncio
    async def test_run_parallel_nodes(self):
        """2 nodes at same level both execute."""
        graph = make_parallel_graph()
        spec = make_test_spec(graph)

        mock_entry = MockNodeExecutor(output_data="normalized")
        mock_p1 = MockNodeExecutor(output_data=[])
        mock_p2 = MockNodeExecutor(output_data=[])
        mock_term = MockNodeExecutor(output_data={"text": "done", "citations": []})

        registry = self._make_registry_with_mocks({
            "normalize_query": mock_entry,
            "retrieve": mock_p1,
            "evidence_cards": mock_p2,
            "synthesize": mock_term,
        })
        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test")

        assert mock_p1.call_count == 1
        assert mock_p2.call_count == 1
        assert "p1" in result.state.node_outputs
        assert "p2" in result.state.node_outputs

    @pytest.mark.asyncio
    async def test_state_merge_normalize_query(self):
        """output_data stored in metadata['normalized_query']."""
        graph = StrategyGraph(
            nodes=[StrategyNode(node_id="nq", node_type="normalize_query", config={})],
            edges=[],
            entry_node="nq",
            terminal_nodes=["nq"],
        )
        spec = make_test_spec(graph)
        mock_exec = MockNodeExecutor(output_data="cleaned query text")
        registry = self._make_registry_with_mocks({"normalize_query": mock_exec})
        runner = StrategyRunner(registry=registry)

        result = await runner.run(spec, make_business_context(), query="test")
        assert result.state.metadata.get("normalized_query") == "cleaned query text"

    @pytest.mark.asyncio
    async def test_state_merge_retrieve(self):
        """output_data sets retrieved_chunks."""
        graph = StrategyGraph(
            nodes=[StrategyNode(node_id="ret", node_type="retrieve", config={})],
            edges=[],
            entry_node="ret",
            terminal_nodes=["ret"],
        )
        spec = make_test_spec(graph)
        chunks_data = [make_test_chunks(1)[0].model_dump()]
        mock_exec = MockNodeExecutor(output_data=chunks_data)
        registry = self._make_registry_with_mocks({"retrieve": mock_exec})
        runner = StrategyRunner(registry=registry)

        result = await runner.run(spec, make_business_context(), query="test")
        assert len(result.state.retrieved_chunks) == 1
        assert result.state.retrieved_chunks[0].chunk_id == "chunk_0"

    @pytest.mark.asyncio
    async def test_state_merge_synthesize(self):
        """output_data sets synthesis_result."""
        graph = StrategyGraph(
            nodes=[StrategyNode(node_id="syn", node_type="synthesize", config={})],
            edges=[],
            entry_node="syn",
            terminal_nodes=["syn"],
        )
        spec = make_test_spec(graph)
        synth_data = {"text": "My answer", "citations": [], "language": "en"}
        mock_exec = MockNodeExecutor(output_data=synth_data)
        registry = self._make_registry_with_mocks({"synthesize": mock_exec})
        runner = StrategyRunner(registry=registry)

        result = await runner.run(spec, make_business_context(), query="test")
        assert result.state.synthesis_result is not None
        assert result.state.synthesis_result.text == "My answer"

    @pytest.mark.asyncio
    async def test_state_merge_validate(self):
        """output_data appends to validation_results."""
        graph = StrategyGraph(
            nodes=[StrategyNode(node_id="val", node_type="validate_citations", config={})],
            edges=[],
            entry_node="val",
            terminal_nodes=["val"],
        )
        spec = make_test_spec(graph)
        val_data = {"validator_id": "citation_check", "passed": True, "score": 0.9, "issues": []}
        mock_exec = MockNodeExecutor(output_data=val_data)
        registry = self._make_registry_with_mocks({"validate_citations": mock_exec})
        runner = StrategyRunner(registry=registry)

        result = await runner.run(spec, make_business_context(), query="test")
        assert len(result.state.validation_results) == 1
        assert result.state.validation_results[0].validator_id == "citation_check"

    @pytest.mark.asyncio
    async def test_budget_halt_on_latency(self):
        """Budget with low latency_hard_limit_ms halts execution."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={}),
                StrategyNode(node_id="n2", node_type="retrieve", config={}),
            ],
            edges=[StrategyEdge(from_node="n1", to_node="n2")],
            entry_node="n1",
            terminal_nodes=["n2"],
        )
        # Set hard limit to 1ms; n1 has delay to exceed it
        budgets = StrategyBudgets(latency_hard_limit_ms=1)
        spec = make_test_spec(graph, budgets=budgets)

        mock_n1 = MockNodeExecutor(output_data="normalized", delay=0.02)
        mock_n2 = MockNodeExecutor(output_data=[])
        registry = self._make_registry_with_mocks({
            "normalize_query": mock_n1,
            "retrieve": mock_n2,
        })
        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test")

        assert result.halt_reason is not None
        assert "latency" in result.halt_reason.lower()

    @pytest.mark.asyncio
    async def test_error_handling_skip(self):
        """Node with on_error='skip' continues after failure."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={},
                             on_error="skip", optional=True),
                StrategyNode(node_id="n2", node_type="synthesize", config={}),
            ],
            edges=[StrategyEdge(from_node="n1", to_node="n2")],
            entry_node="n1",
            terminal_nodes=["n2"],
        )
        spec = make_test_spec(graph)

        mock_n1 = MockNodeExecutor(output_data=None, status="error")
        mock_n2 = MockNodeExecutor(output_data={"text": "result", "citations": []})
        registry = self._make_registry_with_mocks({
            "normalize_query": mock_n1,
            "synthesize": mock_n2,
        })
        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test")

        # n1 fails but skipped; n2 should still execute
        assert "n1" in result.state.node_outputs
        assert "n2" in result.state.node_outputs

    @pytest.mark.asyncio
    async def test_error_handling_halt(self):
        """Node with on_error='halt' stops execution."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={},
                             on_error="halt"),
                StrategyNode(node_id="n2", node_type="synthesize", config={}),
            ],
            edges=[StrategyEdge(from_node="n1", to_node="n2")],
            entry_node="n1",
            terminal_nodes=["n2"],
        )
        spec = make_test_spec(graph)

        mock_n1 = MockNodeExecutor(output_data=None, status="error")
        mock_n2 = MockNodeExecutor(output_data={"text": "result", "citations": []})
        registry = self._make_registry_with_mocks({
            "normalize_query": mock_n1,
            "synthesize": mock_n2,
        })
        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test")

        assert result.halt_reason is not None
        assert "node_halt" in result.halt_reason
        # n2 should NOT have executed
        assert "n2" not in result.state.node_outputs

    @pytest.mark.asyncio
    async def test_state_violation_duplicate_node(self):
        """Executing same node_id twice raises StateViolationError."""
        runner = StrategyRunner(registry=NodeRegistry())
        state = StrategyRunState(query="test")
        output = NodeOutput(
            node_id="dup", node_type="normalize_query", status="success", tokens_used=0
        )
        # First merge
        runner._merge_node_output(state, output)
        # Second merge with same node_id
        with pytest.raises(StateViolationError, match="dup"):
            runner._merge_node_output(state, output)

    @pytest.mark.asyncio
    async def test_conditional_edge_skips_unreachable(self):
        """Node behind false condition is not executed."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="n1", node_type="normalize_query", config={}),
                StrategyNode(node_id="n2", node_type="retrieve", config={}),
            ],
            edges=[
                StrategyEdge(from_node="n1", to_node="n2", condition="chunks_count > 100"),
            ],
            entry_node="n1",
            terminal_nodes=["n2"],
        )
        spec = make_test_spec(graph)

        mock_n1 = MockNodeExecutor(output_data="normalized")
        mock_n2 = MockNodeExecutor(output_data=[])
        registry = self._make_registry_with_mocks({
            "normalize_query": mock_n1,
            "retrieve": mock_n2,
        })
        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test")

        # n1 executes, but n2 is unreachable due to condition=False
        assert "n1" in result.state.node_outputs
        assert "n2" not in result.state.node_outputs


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Node Registry Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestNodeRegistry:
    """Tests for NodeRegistry dispatch and lifecycle."""

    def test_register_and_get_executor(self):
        """Register a mock executor, retrieve it."""
        registry = NodeRegistry()
        registry.register("test_type", MockNodeExecutor)
        executor = registry.get_executor("test_type")
        assert isinstance(executor, MockNodeExecutor)

    def test_unknown_type_raises(self):
        """get_executor for unregistered type raises NodeRegistryError."""
        registry = NodeRegistry()
        with pytest.raises(NodeRegistryError, match="Unknown node type"):
            registry.get_executor("nonexistent_type")

    @pytest.mark.asyncio
    async def test_execute_wraps_timing(self):
        """Output has started_at_ms and duration_ms set."""
        registry = NodeRegistry()
        registry.register("test_type", MockNodeExecutor)
        node = StrategyNode(node_id="t1", node_type="test_type", config={})
        state = StrategyRunState(query="test")

        output = await registry.execute(node, state)
        assert output.started_at_ms > 0
        assert output.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_execute_catches_exception(self):
        """Executor that raises returns error NodeOutput."""
        registry = NodeRegistry()
        registry.register("failing", FailingNodeExecutor)
        node = StrategyNode(node_id="f1", node_type="failing", config={})
        state = StrategyRunState(query="test")

        output = await registry.execute(node, state)
        assert output.status == "error"
        assert "Simulated executor failure" in output.error_message

    def test_default_registry_has_18_types(self):
        """create_default_registry() has 18 registered types."""
        registry = create_default_registry()
        assert registry.type_count == 18

    def test_singleton_instances(self):
        """Same executor instance returned on repeated calls."""
        registry = NodeRegistry()
        registry.register("test_type", MockNodeExecutor)
        inst1 = registry.get_executor("test_type")
        inst2 = registry.get_executor("test_type")
        assert inst1 is inst2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Individual Node Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestIndividualNodes:
    """Tests for specific node executor implementations (no LLM calls)."""

    @pytest.mark.asyncio
    async def test_normalize_query_strips_whitespace(self):
        """'  hello world  ' → 'hello world'."""
        from backend.agent.strategy.nodes.normalize_query import NormalizeQueryExecutor

        executor = NormalizeQueryExecutor()
        node = StrategyNode(node_id="nq", node_type="normalize_query", config={})
        state = StrategyRunState(query="  hello world  ")

        output = await executor.execute(node, state)
        assert output.status == "success"
        assert output.output_data == "hello world"

    @pytest.mark.asyncio
    async def test_normalize_query_detects_german(self):
        """Query with 'Ergänzungsfragen' triggers German detection heuristic."""
        from backend.agent.strategy.nodes.normalize_query import NormalizeQueryExecutor, _detect_language

        # The language detection is internal; verify via the heuristic directly
        lang = _detect_language("Erstelle Ergänzungsfragen für den Gutachter")
        assert lang == "de"

    @pytest.mark.asyncio
    async def test_boilerplate_filter_removes_short(self):
        """Chunks with <20 char content are filtered out."""
        from backend.agent.strategy.nodes.boilerplate_filter import BoilerplateFilterExecutor

        executor = BoilerplateFilterExecutor()
        node = StrategyNode(node_id="bf", node_type="boilerplate_filter", config={})

        short_chunk = RetrievedChunk(
            chunk_id="short", document_id="d1", document_title="Doc",
            source_id="s1", content="Too short", score=0.9, search_type="semantic",
        )
        good_chunk = RetrievedChunk(
            chunk_id="good", document_id="d2", document_title="Doc",
            source_id="s2",
            content="This is a sufficiently long chunk of text that should pass the filter easily.",
            score=0.8, search_type="semantic",
        )
        state = StrategyRunState(query="test", retrieved_chunks=[short_chunk, good_chunk])
        output = await executor.execute(node, state)

        assert output.status == "success"
        # output_data is list of dicts
        assert len(output.output_data) == 1
        assert output.output_data[0]["chunk_id"] == "good"

    @pytest.mark.asyncio
    async def test_dedupe_versions_keeps_highest_score(self):
        """Duplicate document_id chunks — keeps highest score."""
        from backend.agent.strategy.nodes.dedupe_versions import DedupeVersionsExecutor

        executor = DedupeVersionsExecutor()
        node = StrategyNode(node_id="dv", node_type="dedupe_versions",
                            config={"prefer": "highest_score"})

        chunk_a = RetrievedChunk(
            chunk_id="a", document_id="doc_1", document_title="Doc",
            source_id="s1", content="Content A with enough text for filter.", score=0.7,
            search_type="semantic",
        )
        chunk_b = RetrievedChunk(
            chunk_id="b", document_id="doc_1", document_title="Doc",
            source_id="s1", content="Content B with enough text for filter.", score=0.9,
            search_type="semantic",
        )
        state = StrategyRunState(query="test", retrieved_chunks=[chunk_a, chunk_b])
        output = await executor.execute(node, state)

        assert output.status == "success"
        assert len(output.output_data) == 1
        assert output.output_data[0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_evidence_cards_generates_cards(self):
        """Given chunks, produces EvidenceCard-shaped output."""
        from backend.agent.strategy.nodes.evidence_cards import EvidenceCardExecutor

        executor = EvidenceCardExecutor()
        node = StrategyNode(node_id="ec", node_type="evidence_cards", config={})
        chunks = make_test_chunks(3)
        state = StrategyRunState(query="test", retrieved_chunks=chunks)

        output = await executor.execute(node, state)
        assert output.status == "success"
        assert isinstance(output.output_data, list)
        assert len(output.output_data) >= 1
        # Each card should have required fields
        card = output.output_data[0]
        assert "card_id" in card
        assert "topic" in card
        assert "confidence" in card

    @pytest.mark.asyncio
    async def test_synthesize_produces_result(self):
        """Given evidence cards in state, produces SynthesisResult."""
        from backend.agent.strategy.nodes.synthesize import SynthesizeExecutor

        executor = SynthesizeExecutor()
        node = StrategyNode(node_id="syn", node_type="synthesize", config={})

        cards = [
            EvidenceCard(
                card_type="fact",
                topic="Test topic",
                source_id="src_0",
                document_title="Doc 0",
                source_excerpt="Some excerpt text for testing purposes.",
                factual_basis="Based on source.",
                confidence=0.85,
            )
        ]
        state = StrategyRunState(query="What is the answer?", evidence_cards=cards)

        output = await executor.execute(node, state)
        assert output.status == "success"
        assert isinstance(output.output_data, dict)
        assert "text" in output.output_data
        assert "citations" in output.output_data
        assert len(output.output_data["text"]) > 0

    @pytest.mark.asyncio
    async def test_validate_citations_checks_sources(self):
        """Citations referencing known chunks pass validation."""
        from backend.agent.strategy.nodes.validate_citations import ValidateCitationsExecutor

        executor = ValidateCitationsExecutor()
        node = StrategyNode(node_id="vc", node_type="validate_citations", config={})

        chunks = make_test_chunks(2)
        synthesis = SynthesisResult(
            text="Based on Document 0 and Document 1.",
            citations=[
                CitationRef(source_id="src_0", document_title="Document 0"),
                CitationRef(source_id="src_1", document_title="Document 1"),
            ],
            language="en",
        )
        state = StrategyRunState(
            query="test",
            retrieved_chunks=chunks,
            synthesis_result=synthesis,
        )

        output = await executor.execute(node, state)
        assert output.status == "success"
        result_data = output.output_data
        assert result_data["passed"] is True
        assert result_data["metadata"]["orphan_count"] == 0

    @pytest.mark.asyncio
    async def test_refine_removes_orphan_citations(self):
        """Orphan citations get removed from synthesis."""
        from backend.agent.strategy.nodes.refine import RefineExecutor

        executor = RefineExecutor()
        node = StrategyNode(node_id="ref", node_type="refine", config={})

        # Synthesis with a citation whose source_id is NOT in text
        synthesis = SynthesisResult(
            text="Based on Document Alpha content here.",
            citations=[
                CitationRef(source_id="src_alpha", document_title="Document Alpha"),
                CitationRef(source_id="src_orphan", document_title="Orphan Doc"),
            ],
            language="en",
            confidence=0.9,
        )
        # Validation flagging orphan
        validation = ValidationResult(
            validator_id="citation_check",
            passed=False,
            score=0.5,
            issues=[
                ValidationIssue(
                    issue_type="orphan_citation",
                    severity="error",
                    message="Citation to src_orphan not in chunks",
                    field="src_orphan",
                )
            ],
        )
        state = StrategyRunState(
            query="test",
            synthesis_result=synthesis,
            validation_results=[validation],
        )

        output = await executor.execute(node, state)
        assert output.status == "success"
        refined = output.output_data
        # Orphan citation should be removed
        remaining_sources = [c["source_id"] for c in refined["citations"]]
        assert "src_orphan" not in remaining_sources


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullDAGExecution:
    """End-to-end DAG execution tests."""

    @pytest.mark.asyncio
    async def test_full_dag_with_mock_nodes(self):
        """Build a realistic 5-node graph, run with mocks, verify final state."""
        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="normalize", node_type="normalize_query", config={}),
                StrategyNode(node_id="retrieve", node_type="retrieve", config={}),
                StrategyNode(node_id="evidence", node_type="evidence_cards", config={}),
                StrategyNode(node_id="synth", node_type="synthesize", config={}),
                StrategyNode(node_id="validate", node_type="validate_citations", config={}),
            ],
            edges=[
                StrategyEdge(from_node="normalize", to_node="retrieve"),
                StrategyEdge(from_node="retrieve", to_node="evidence"),
                StrategyEdge(from_node="evidence", to_node="synth"),
                StrategyEdge(from_node="synth", to_node="validate"),
            ],
            entry_node="normalize",
            terminal_nodes=["validate"],
        )
        spec = make_test_spec(graph)

        chunks = [make_test_chunks(2)[0].model_dump()]
        cards = [EvidenceCard(
            card_type="fact", topic="Test", source_id="src_0",
            document_title="Doc 0", source_excerpt="Excerpt",
            factual_basis="Based on source.", confidence=0.9,
        ).model_dump()]
        synth = SynthesisResult(
            text="Answer text", citations=[], language="en"
        ).model_dump()
        val = ValidationResult(
            validator_id="citation_check", passed=True, score=1.0
        ).model_dump()

        registry = NodeRegistry()
        # Register mock executors for each type
        mock_classes = {
            "normalize_query": MockNodeExecutor(output_data="normalized query"),
            "retrieve": MockNodeExecutor(output_data=chunks),
            "evidence_cards": MockNodeExecutor(output_data=cards),
            "synthesize": MockNodeExecutor(output_data=synth),
            "validate_citations": MockNodeExecutor(output_data=val),
        }
        for ntype, mock_inst in mock_classes.items():
            registry._executors[ntype] = type(mock_inst)
            registry._instances[ntype] = mock_inst

        runner = StrategyRunner(registry=registry)
        result = await runner.run(spec, make_business_context(), query="test question")

        # All 5 nodes should have executed
        assert len(result.state.node_outputs) == 5
        assert result.state.synthesis_result is not None
        assert result.state.synthesis_result.text == "Answer text"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_full_dag_with_real_simple_nodes(self):
        """Use actual NormalizeQueryExecutor + BoilerplateFilterExecutor in a 2-node graph."""
        from backend.agent.strategy.nodes.normalize_query import NormalizeQueryExecutor
        from backend.agent.strategy.nodes.boilerplate_filter import BoilerplateFilterExecutor

        graph = StrategyGraph(
            nodes=[
                StrategyNode(node_id="norm", node_type="normalize_query", config={}),
                StrategyNode(node_id="filter", node_type="boilerplate_filter", config={}),
            ],
            edges=[StrategyEdge(from_node="norm", to_node="filter")],
            entry_node="norm",
            terminal_nodes=["filter"],
        )
        spec = make_test_spec(graph)

        registry = NodeRegistry()
        registry.register("normalize_query", NormalizeQueryExecutor)
        registry.register("boilerplate_filter", BoilerplateFilterExecutor)

        runner = StrategyRunner(registry=registry)

        # Pre-populate state with chunks so filter has input
        chunks = make_test_chunks(2)
        # We run with query that has whitespace
        result = await runner.run(spec, make_business_context(), query="  what is this  ")

        assert "norm" in result.state.node_outputs
        assert result.state.node_outputs["norm"].status == "success"
        # Normalized query should be stripped
        assert result.state.metadata.get("normalized_query") == "what is this"
