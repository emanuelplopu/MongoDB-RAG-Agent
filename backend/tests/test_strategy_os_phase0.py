"""
Unit tests for Strategy OS Phase 0 (Safety Baseline).

Tests verify:
- Pydantic models instantiate correctly with defaults
- StrategyRunState append-only semantics
- ModelRoleRegistry initialization and fallback behavior
- LegacyStrategyAdapter spec generation and output mapping
- Telemetry schema backward compatibility
"""

import pytest
from unittest.mock import MagicMock


class TestStrategyRunState:
    """Tests for StrategyRunState model."""

    def test_creation_with_defaults(self):
        """StrategyRunState initializes with proper defaults."""
        from backend.agent.strategy.models import StrategyRunState

        state = StrategyRunState(query="test query")
        assert state.query == "test query"
        assert state.run_id  # UUID generated
        assert state.trace_id  # UUID generated
        assert state.retrieved_chunks == []
        assert state.evidence_cards == []
        assert state.synthesis_result is None
        assert state.validation_results == []
        assert state.node_outputs == {}
        assert state.elapsed_ms == 0.0
        assert state.cancelled is False

    def test_node_output_accumulation(self):
        """NodeOutputs accumulate in state dict."""
        from backend.agent.strategy.models import NodeOutput, StrategyRunState

        state = StrategyRunState(query="test")

        output1 = NodeOutput(node_id="retrieve", node_type="retrieve", duration_ms=100)
        state.node_outputs["retrieve"] = output1

        output2 = NodeOutput(
            node_id="synthesize", node_type="synthesize", duration_ms=200
        )
        state.node_outputs["synthesize"] = output2

        assert len(state.node_outputs) == 2
        assert state.node_outputs["retrieve"].duration_ms == 100
        assert state.node_outputs["synthesize"].duration_ms == 200

    def test_retrieved_chunks_append(self):
        """Retrieved chunks can be appended to state."""
        from backend.agent.strategy.models import RetrievedChunk, StrategyRunState

        state = StrategyRunState(query="test")

        chunk = RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            document_title="Doc 1",
            source_id="s1",
            content="Hello world",
            score=0.95,
            search_type="semantic",
        )
        state.retrieved_chunks.append(chunk)
        assert len(state.retrieved_chunks) == 1
        assert state.retrieved_chunks[0].score == 0.95

    def test_unique_run_ids(self):
        """Each StrategyRunState gets unique run_id and trace_id."""
        from backend.agent.strategy.models import StrategyRunState

        state1 = StrategyRunState(query="q1")
        state2 = StrategyRunState(query="q2")
        assert state1.run_id != state2.run_id
        assert state1.trace_id != state2.trace_id

    def test_metadata_dict(self):
        """Metadata dict can store arbitrary data."""
        from backend.agent.strategy.models import StrategyRunState

        state = StrategyRunState(query="test")
        state.metadata["key"] = "value"
        state.metadata["nested"] = {"a": 1}
        assert state.metadata["key"] == "value"
        assert state.metadata["nested"]["a"] == 1


class TestStrategySpec:
    """Tests for StrategySpec model."""

    def test_minimal_spec_creation(self):
        """StrategySpec can be created with minimal required fields."""
        from backend.agent.strategy.models import (
            StrategyGraph,
            StrategyNode,
            StrategySpec,
        )

        spec = StrategySpec(
            strategy_id="test_strategy_v1",
            graph=StrategyGraph(
                nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
                edges=[],
                entry_node="n1",
                terminal_nodes=["n1"],
            ),
        )
        assert spec.strategy_id == "test_strategy_v1"
        assert spec.status == "draft"
        assert spec.version == "1.0.0"
        assert len(spec.graph.nodes) == 1

    def test_spec_with_policies(self):
        """StrategySpec policies have sensible defaults."""
        from backend.agent.strategy.models import (
            SourcePolicy,
            StrategyBudgets,
            StrategyGraph,
            StrategyNode,
            StrategySpec,
        )

        spec = StrategySpec(
            strategy_id="test_v1",
            graph=StrategyGraph(
                nodes=[StrategyNode(node_id="n1", node_type="synthesize")],
                edges=[],
                entry_node="n1",
                terminal_nodes=["n1"],
            ),
            source_policy=SourcePolicy(allow_web=False, allow_cross_matter=False),
            budgets=StrategyBudgets(latency_target_ms=5000),
        )
        assert spec.source_policy.allow_web is False
        assert spec.budgets.latency_target_ms == 5000
        assert spec.budgets.latency_hard_limit_ms == 45000  # default

    def test_spec_default_policies(self):
        """StrategySpec generates default policies when not provided."""
        from backend.agent.strategy.models import (
            StrategyGraph,
            StrategyNode,
            StrategySpec,
        )

        spec = StrategySpec(
            strategy_id="test_defaults",
            graph=StrategyGraph(
                nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
                edges=[],
                entry_node="n1",
                terminal_nodes=["n1"],
            ),
        )
        assert spec.retrieval_policy.search_type == "hybrid"
        assert spec.retrieval_policy.top_k == 10
        assert spec.evidence_policy.enabled is True
        assert spec.validation_policy.answer_contract_check is True
        assert spec.budgets.max_llm_calls == 10

    def test_strategy_node_defaults(self):
        """StrategyNode has correct defaults for error/empty handling."""
        from backend.agent.strategy.models import StrategyNode

        node = StrategyNode(node_id="test", node_type="retrieve")
        assert node.optional is False
        assert node.on_empty == "skip"
        assert node.on_error == "halt"
        assert node.model_role is None
        assert node.timeout_ms is None


class TestModelRoleRegistry:
    """Tests for ModelRoleRegistry."""

    def _make_mock_settings(self):
        """Create a mock BackendSettings with required fields."""
        settings = MagicMock()
        settings.llm_provider = "openai"
        settings.llm_model = "gpt-5.2"
        settings.fast_llm_provider = "google"
        settings.fast_llm_model = "gemini-2.0-flash-exp"
        settings.agent_orchestrator_timeout = 120
        settings.agent_worker_timeout = 60
        return settings

    def test_init_from_settings(self):
        """Registry populates roles from settings."""
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        assert len(registry) >= 10  # orchestrator + worker + 2 derived + 6 placeholder
        assert "orchestrator" in registry
        assert "worker" in registry
        assert "synthesizer_fast" in registry
        assert "synthesizer_deep" in registry

    def test_init_without_settings(self):
        """Registry can be created empty without settings."""
        from backend.core.model_roles import ModelRoleRegistry

        registry = ModelRoleRegistry()
        assert len(registry) == 0

    def test_get_role(self):
        """get_role returns correct config."""
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        role = registry.get_role("orchestrator")
        assert role.provider == "openai"
        assert role.model == "gpt-5.2"

    def test_get_role_missing_raises(self):
        """get_role raises KeyError for unknown role."""
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        with pytest.raises(KeyError):
            registry.get_role("nonexistent_role")

    def test_get_model_for_role(self):
        """get_model_for_role returns (provider, model) tuple."""
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        provider, model = registry.get_model_for_role("worker")
        assert provider == "google"
        assert model == "gemini-2.0-flash-exp"

    def test_fallback_resolution(self):
        """resolve_with_fallback uses worker as ultimate fallback."""
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        # Unknown role should fall back to worker
        role = registry.resolve_with_fallback("totally_unknown_role")
        assert role.role_id == "worker"

    def test_validate_spec_roles_empty(self):
        """validate_spec_roles returns empty list when all roles exist."""
        from backend.agent.strategy.models import (
            StrategyGraph,
            StrategyNode,
            StrategySpec,
        )
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        spec = StrategySpec(
            strategy_id="test",
            graph=StrategyGraph(
                nodes=[
                    StrategyNode(
                        node_id="n1", node_type="synthesize", model_role="orchestrator"
                    )
                ],
                edges=[],
                entry_node="n1",
                terminal_nodes=["n1"],
            ),
        )
        missing = registry.validate_spec_roles(spec)
        assert missing == []

    def test_validate_spec_roles_missing(self):
        """validate_spec_roles detects missing roles."""
        from backend.agent.strategy.models import (
            StrategyGraph,
            StrategyNode,
            StrategySpec,
        )
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        spec = StrategySpec(
            strategy_id="test",
            graph=StrategyGraph(
                nodes=[
                    StrategyNode(
                        node_id="n1",
                        node_type="synthesize",
                        model_role="nonexistent_model",
                    )
                ],
                edges=[],
                entry_node="n1",
                terminal_nodes=["n1"],
            ),
        )
        missing = registry.validate_spec_roles(spec)
        assert "nonexistent_model" in missing

    def test_register_custom_role(self):
        """Custom roles can be registered dynamically."""
        from backend.core.model_roles import ModelRoleConfig, ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        custom = ModelRoleConfig(
            role_id="custom_role",
            provider="anthropic",
            model="claude-4",
        )
        registry.register_role(custom)

        assert "custom_role" in registry
        role = registry.get_role("custom_role")
        assert role.provider == "anthropic"
        assert role.model == "claude-4"

    def test_list_roles(self):
        """list_roles returns all registered roles."""
        from backend.core.model_roles import ModelRoleRegistry

        settings = self._make_mock_settings()
        registry = ModelRoleRegistry(settings)

        roles = registry.list_roles()
        assert isinstance(roles, dict)
        assert "orchestrator" in roles
        assert "worker" in roles


class TestLegacyStrategyAdapter:
    """Tests for LegacyStrategyAdapter."""

    def test_get_spec(self):
        """Adapter produces a valid StrategySpec."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter

        adapter = LegacyStrategyAdapter()
        spec = adapter.get_spec()

        assert spec.strategy_id == "legacy_orchestrator_v1"
        assert spec.status == "active"
        assert len(spec.graph.nodes) == 1
        assert spec.graph.nodes[0].node_type == "legacy_orchestrator_pipeline"
        assert spec.spec_hash is not None
        assert len(spec.spec_hash) == 64  # SHA-256 hex

    def test_spec_hash_deterministic(self):
        """Spec hash is the same across multiple calls."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter

        adapter = LegacyStrategyAdapter()
        hash1 = adapter.get_spec_hash()
        hash2 = adapter.get_spec_hash()
        assert hash1 == hash2

    def test_spec_cached(self):
        """get_spec returns the same instance on repeated calls."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter

        adapter = LegacyStrategyAdapter()
        spec1 = adapter.get_spec()
        spec2 = adapter.get_spec()
        assert spec1 is spec2

    def test_wrap_execution(self):
        """wrap_execution maps outputs to StrategyRunState."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter

        adapter = LegacyStrategyAdapter()

        state = adapter.wrap_execution(
            query="Was ist der Stand?",
            response_text="Der aktuelle Stand ist...",
            phase_metrics=[
                {"phase": "analyze", "duration_ms": 500, "tokens_used": 100},
                {"phase": "synthesize", "duration_ms": 2000, "tokens_used": 500},
            ],
            search_operations=[],
            llm_calls=[
                {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            ],
            total_duration_ms=3000,
        )

        assert state.query == "Was ist der Stand?"
        assert state.synthesis_result is not None
        assert state.synthesis_result.text == "Der aktuelle Stand ist..."
        assert state.elapsed_ms == 3000
        assert "legacy_orchestrator_pipeline" in state.node_outputs
        assert len(state.node_outputs) >= 3  # 2 phases + overall

    def test_wrap_execution_empty_inputs(self):
        """wrap_execution handles None/empty optional inputs gracefully."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter

        adapter = LegacyStrategyAdapter()

        state = adapter.wrap_execution(
            query="test",
            response_text="response",
            phase_metrics=None,
            search_operations=None,
            llm_calls=None,
            total_duration_ms=1000,
        )

        assert state.query == "test"
        assert state.synthesis_result.text == "response"
        assert state.elapsed_ms == 1000
        # Only the overall pipeline node output when no phase_metrics
        assert "legacy_orchestrator_pipeline" in state.node_outputs

    def test_generate_node_metrics(self):
        """generate_node_metrics produces valid StrategyNodeMetric list."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter
        from backend.agent.strategy.models import StrategyRunState

        adapter = LegacyStrategyAdapter()

        state = StrategyRunState(query="test", elapsed_ms=5000)
        metrics = adapter.generate_node_metrics(
            state=state,
            phase_metrics=[
                {"phase": "analyze", "duration_ms": 1000},
                {"phase": "execute", "duration_ms": 3000},
            ],
            llm_calls=[{"prompt_tokens": 500, "completion_tokens": 200}],
            search_operations=[{}, {}],
        )

        assert len(metrics) >= 3  # 2 phases + overall
        # Check overall metric
        overall = [m for m in metrics if m.node_id == "legacy_orchestrator_pipeline"]
        assert len(overall) == 1
        assert overall[0].search_count == 2
        assert overall[0].tokens_in == 500

    def test_generate_node_metrics_empty(self):
        """generate_node_metrics handles no phase_metrics."""
        from backend.agent.strategy.legacy_adapter import LegacyStrategyAdapter
        from backend.agent.strategy.models import StrategyRunState

        adapter = LegacyStrategyAdapter()
        state = StrategyRunState(query="test", elapsed_ms=1000)

        metrics = adapter.generate_node_metrics(
            state=state,
            phase_metrics=None,
            llm_calls=None,
            search_operations=None,
        )

        # Should still have the overall pipeline metric
        assert len(metrics) == 1
        assert metrics[0].node_id == "legacy_orchestrator_pipeline"

    def test_get_telemetry_fields(self):
        """get_telemetry_fields returns correct Strategy OS fields."""
        from backend.agent.strategy.legacy_adapter import (
            LEGACY_STRATEGY_ID,
            LegacyStrategyAdapter,
        )
        from backend.agent.strategy.models import StrategyRunState

        adapter = LegacyStrategyAdapter()

        state = StrategyRunState(query="test")
        fields = adapter.get_telemetry_fields(state)

        assert fields["strategy_id"] == LEGACY_STRATEGY_ID
        assert fields["telemetry_schema_version"] == 1
        assert fields["trace_id"] == state.trace_id
        assert "strategy_spec_hash" in fields
        assert len(fields["strategy_spec_hash"]) == 64
        assert fields["strategy_version"] == "1.0.0"
        assert fields["strategy_status"] == "active"


class TestTelemetryBackwardCompat:
    """Tests for telemetry schema backward compatibility."""

    def test_legacy_record_still_valid(self):
        """Existing TelemetryRecord fields still work without new Strategy OS fields."""
        from backend.models.telemetry import TelemetryRecord

        # Create record with only legacy fields (no strategy fields)
        record = TelemetryRecord(
            record_id="test-123",
            session_id="session-456",
            user_id="user-anon",
            tenant="recallhub",
            timestamp="2026-01-01T00:00:00Z",
        )
        assert record.strategy_id is None
        assert record.strategy_node_metrics is None
        assert record.telemetry_schema_version == 1

    def test_record_with_strategy_fields(self):
        """TelemetryRecord accepts new strategy fields."""
        from backend.models.telemetry import StrategyNodeMetric, TelemetryRecord

        record = TelemetryRecord(
            record_id="test-789",
            session_id="session-abc",
            user_id="user-anon",
            tenant="quellex",
            timestamp="2026-01-01T00:00:00Z",
            strategy_id="legacy_orchestrator_v1",
            telemetry_schema_version=2,
            strategy_node_metrics=[
                StrategyNodeMetric(
                    node_id="retrieve",
                    node_type="retrieve",
                    duration_ms=150,
                    success=True,
                )
            ],
        )
        assert record.strategy_id == "legacy_orchestrator_v1"
        assert record.telemetry_schema_version == 2
        assert len(record.strategy_node_metrics) == 1

    def test_strategy_node_metric_model(self):
        """StrategyNodeMetric initializes correctly."""
        from backend.models.telemetry import StrategyNodeMetric

        metric = StrategyNodeMetric(
            node_id="evidence_cards",
            node_type="evidence_cards",
            duration_ms=2500,
            model_role="synthesizer_fast",
            model_used="gemini-2.0-flash-exp",
            tokens_in=5000,
            tokens_out=1200,
            success=True,
        )
        assert metric.node_id == "evidence_cards"
        assert metric.tokens_in == 5000
        assert metric.error is None

    def test_strategy_node_metric_defaults(self):
        """StrategyNodeMetric has correct defaults for optional fields."""
        from backend.models.telemetry import StrategyNodeMetric

        metric = StrategyNodeMetric(
            node_id="test_node",
            node_type="test",
        )
        assert metric.duration_ms == 0.0
        assert metric.model_role is None
        assert metric.model_used is None
        assert metric.tokens_in == 0
        assert metric.tokens_out == 0
        assert metric.search_count == 0
        assert metric.sources_in == 0
        assert metric.sources_out == 0
        assert metric.success is True
        assert metric.error is None

    def test_record_optional_strategy_fields(self):
        """All new strategy fields are optional and default to None/1."""
        from backend.models.telemetry import TelemetryRecord

        record = TelemetryRecord(
            record_id="compat-test",
            session_id="s1",
            user_id="u1",
            tenant="recallhub",
        )
        assert record.strategy_id is None
        assert record.strategy_version is None
        assert record.strategy_spec_hash is None
        assert record.strategy_status is None
        assert record.capability_id is None
        assert record.answer_contract_id is None
        assert record.telemetry_schema_version == 1
        assert record.strategy_node_metrics is None
        assert record.experiment_id is None
        assert record.trace_id is None
