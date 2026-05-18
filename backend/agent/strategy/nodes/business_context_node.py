"""BusinessContextNodeExecutor — Resolve business context within the DAG."""
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.agent.strategy.business_context_resolver import BusinessContextResolver
from backend.agent.strategy.models import (
    NodeOutput,
    StrategyNode,
    StrategyRunState,
)
from backend.agent.strategy.nodes.base import NodeExecutor

logger = logging.getLogger(__name__)


class BusinessContextNodeExecutor(NodeExecutor):
    """
    Wraps :class:`BusinessContextResolver` as a DAG node so that
    business context can be resolved *within* a strategy graph rather
    than only before execution starts.

    Dependency injection:
        ``resolver`` may be set externally. If it is ``None`` at
        execution time, a resolver is created on-the-fly using
        ``tenant_id`` from ``node.config``.

    Output:
        Serialized ``BusinessContext`` dict stored in ``output_data``.
    """

    def __init__(self, resolver: Optional[BusinessContextResolver] = None) -> None:
        self.resolver: Optional[BusinessContextResolver] = resolver

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        query = accessor.get_normalized_query() or accessor.get_query()

        if not query:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
                error_message="No query available for business context resolution",
            )

        # Ensure we have a resolver
        resolver = self.resolver
        if resolver is None:
            tenant_id: str = node.config.get("tenant_id", "recallhub")
            config_path: str = node.config.get("config_path", "backend/config")
            resolver = BusinessContextResolver(tenant_id=tenant_id, config_path=config_path)

        profile_key: Optional[str] = node.config.get("profile_key")
        matter_id: Optional[str] = node.config.get("matter_id")
        accessible_profiles: Optional[list[str]] = node.config.get("accessible_profiles")

        try:
            business_context = await resolver.resolve(
                query=query,
                profile_key=profile_key,
                matter_id=matter_id,
                accessible_profiles=accessible_profiles,
            )
        except Exception as exc:
            logger.error(
                "BusinessContextResolver.resolve() failed for node '%s': %s",
                node.node_id,
                exc,
                exc_info=True,
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="error",
                error_message=f"Business context resolution failed: {exc}",
                tokens_used=0,
            )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=business_context.model_dump(),
            tokens_used=0,
        )
