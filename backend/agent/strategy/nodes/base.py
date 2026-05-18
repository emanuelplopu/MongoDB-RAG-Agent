"""Base classes for Strategy OS node executors."""
from __future__ import annotations

import copy
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from backend.agent.strategy.models import (
    StrategyNode,
    StrategyRunState,
    NodeOutput,
    RetrievedChunk,
    EvidenceCard,
    SynthesisResult,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class StateAccessor:
    """
    Read-only accessor for StrategyRunState.

    All getters return deep copies to ensure node isolation —
    parallel nodes cannot accidentally share or mutate state.
    """

    def __init__(self, state: StrategyRunState):
        self._state = state

    def get_query(self) -> str:
        """Get the original user query."""
        return self._state.query

    def get_normalized_query(self) -> Optional[str]:
        """Get normalized query if available, else original."""
        return self._state.metadata.get("normalized_query", self._state.query)

    def get_chunks(self) -> list[RetrievedChunk]:
        """Get retrieved chunks (deep copy for isolation)."""
        return copy.deepcopy(self._state.retrieved_chunks)

    def get_evidence_cards(self) -> list[EvidenceCard]:
        """Get evidence cards (deep copy for isolation)."""
        return copy.deepcopy(self._state.evidence_cards)

    def get_synthesis(self) -> Optional[SynthesisResult]:
        """Get current synthesis result (deep copy for isolation)."""
        if self._state.synthesis_result is None:
            return None
        return self._state.synthesis_result.model_copy(deep=True)

    def get_validation_results(self) -> list[ValidationResult]:
        """Get validation results (deep copy for isolation)."""
        return copy.deepcopy(self._state.validation_results)

    def get_business_context(self) -> Optional[Any]:
        """Get business context from state."""
        return copy.deepcopy(self._state.business_context)

    def get_node_output(self, node_id: str) -> Optional[NodeOutput]:
        """Get output of a specific completed node (deep copy)."""
        outputs = self._state.node_outputs or {}
        if node_id in outputs:
            return copy.deepcopy(outputs[node_id])
        return None

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata value by key."""
        return copy.deepcopy(self._state.metadata.get(key, default))

    def get_elapsed_ms(self) -> float:
        """Get current elapsed time in milliseconds."""
        return self._state.elapsed_ms or 0.0


class NodeExecutor(ABC):
    """
    Abstract base class for all node type implementations.

    Subclasses implement execute() which:
    1. Reads input from state via StateAccessor (deepcopy isolation)
    2. Performs node-specific logic
    3. Returns NodeOutput with result in output_data

    Nodes NEVER mutate state directly. The StrategyRunner handles
    merging NodeOutput into state after successful execution.
    """

    def get_state_accessor(self, state: StrategyRunState) -> StateAccessor:
        """Create a read-only state accessor for this node."""
        return StateAccessor(state)

    @abstractmethod
    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """
        Execute this node type.

        Args:
            node: The StrategyNode definition (contains config, model_role, etc.)
            state: Current run state (use get_state_accessor for safe read access)

        Returns:
            NodeOutput with status and output_data populated.
            Node implementations should set:
            - node_id = node.node_id
            - node_type = node.node_type
            - status = "success" | "empty" | "error"
            - output_data = the typed result
            - tokens_used (if LLM was called)
            - error_message (if status is "error")
        """
        ...
