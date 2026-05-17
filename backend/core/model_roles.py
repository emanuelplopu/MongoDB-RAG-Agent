"""
Model Role Registry for Strategy OS.

Maps abstract model roles (orchestrator, worker, synthesizer_fast, etc.)
to concrete LLM provider/model configurations. Provides fallback resolution
and strategy spec validation.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

import logging

logger = logging.getLogger(__name__)


class ModelRoleConfig(BaseModel):
    """Configuration for a single model role."""

    role_id: str
    provider: str  # e.g. "openai", "google", "ollama"
    model: str  # e.g. "gpt-5.2", "gemini-2.0-flash-exp"
    api_key_env: Optional[str] = None  # env var name for API key
    fallback_role: Optional[str] = None  # role_id to fall back to
    capabilities: list[str] = Field(default_factory=list)  # ["text_completion", "streaming", "json_mode", etc.]
    max_context_tokens: int = 32000
    timeout_ms: int = 60000


class ModelRoleRegistry:
    """
    Central registry mapping role IDs to model configurations.
    Initialized from BackendSettings; can be extended with YAML config.
    """

    def __init__(self, settings=None):
        """
        Initialize registry from BackendSettings.
        Populates initial roles from existing orchestrator/worker config.
        """
        self._roles: dict[str, ModelRoleConfig] = {}
        if settings:
            self._populate_from_settings(settings)

    def _populate_from_settings(self, settings) -> None:
        """Populate initial roles from existing BackendSettings fields."""
        # Primary roles from existing dual-model config
        self._roles["orchestrator"] = ModelRoleConfig(
            role_id="orchestrator",
            provider=getattr(settings, "llm_provider", "openai"),
            model=getattr(settings, "llm_model", "gpt-5.2"),
            api_key_env="LLM_API_KEY",
            capabilities=["text_completion", "streaming", "long_context", "json_mode"],
            max_context_tokens=128000,
            timeout_ms=getattr(settings, "agent_orchestrator_timeout", 120) * 1000,
        )

        self._roles["worker"] = ModelRoleConfig(
            role_id="worker",
            provider=getattr(settings, "fast_llm_provider", "google"),
            model=getattr(settings, "fast_llm_model", "gemini-2.0-flash-exp"),
            api_key_env="FAST_LLM_API_KEY",
            fallback_role=None,  # worker is the universal last-resort
            capabilities=["text_completion", "streaming", "low_latency"],
            max_context_tokens=32000,
            timeout_ms=getattr(settings, "agent_worker_timeout", 60) * 1000,
        )

        # Derived roles (aliases pointing to same underlying model)
        self._roles["synthesizer_fast"] = ModelRoleConfig(
            role_id="synthesizer_fast",
            provider=self._roles["worker"].provider,
            model=self._roles["worker"].model,
            api_key_env=self._roles["worker"].api_key_env,
            fallback_role="worker",
            capabilities=["text_completion", "streaming"],
            max_context_tokens=32000,
            timeout_ms=60000,
        )

        self._roles["synthesizer_deep"] = ModelRoleConfig(
            role_id="synthesizer_deep",
            provider=self._roles["orchestrator"].provider,
            model=self._roles["orchestrator"].model,
            api_key_env=self._roles["orchestrator"].api_key_env,
            fallback_role="orchestrator",
            capabilities=["text_completion", "streaming", "long_context"],
            max_context_tokens=128000,
            timeout_ms=120000,
        )

        # Placeholder roles (default to worker until explicitly configured)
        for role_id in ["classifier", "query_expander", "reranker", "evidence_compressor", "judge", "validator"]:
            self._roles[role_id] = ModelRoleConfig(
                role_id=role_id,
                provider=self._roles["worker"].provider,
                model=self._roles["worker"].model,
                api_key_env=self._roles["worker"].api_key_env,
                fallback_role="worker",
                capabilities=["text_completion"],
                max_context_tokens=32000,
                timeout_ms=60000,
            )

        logger.info(f"ModelRoleRegistry initialized with {len(self._roles)} roles")

    def get_role(self, role_id: str) -> ModelRoleConfig:
        """Get role config by ID. Raises KeyError if not found."""
        if role_id not in self._roles:
            raise KeyError(f"Model role '{role_id}' not registered. Available: {list(self._roles.keys())}")
        return self._roles[role_id]

    def get_model_for_role(self, role_id: str) -> tuple[str, str]:
        """Get (provider, model) tuple for a role. Applies fallback if primary unavailable."""
        config = self.get_role(role_id)
        return (config.provider, config.model)

    def resolve_with_fallback(self, role_id: str) -> ModelRoleConfig:
        """
        Resolve role with fallback chain.
        If role exists, returns it. If not, follows fallback_role chain.
        Ultimate fallback is always 'worker'.
        """
        visited: set[str] = set()
        current_id: Optional[str] = role_id

        while current_id and current_id not in visited:
            visited.add(current_id)
            if current_id in self._roles:
                return self._roles[current_id]
            # Try to find fallback from a known role
            for known_role in self._roles.values():
                if known_role.role_id == current_id:
                    current_id = known_role.fallback_role
                    break
            else:
                break

        # Ultimate fallback
        if "worker" in self._roles:
            logger.warning(f"Role '{role_id}' not found, falling back to 'worker'")
            return self._roles["worker"]

        raise KeyError(f"Cannot resolve role '{role_id}' - no fallback available")

    def validate_spec_roles(self, spec) -> list[str]:
        """
        Validate that all model_roles referenced in a StrategySpec are registered.
        Returns list of missing role IDs (empty list = all valid).
        """
        missing: list[str] = []
        # Check spec.model_roles dict
        if hasattr(spec, "model_roles") and spec.model_roles:
            for role_id in spec.model_roles:
                if role_id not in self._roles:
                    missing.append(role_id)

        # Check node-level model_role references
        if hasattr(spec, "graph") and spec.graph:
            for node in spec.graph.nodes:
                if node.model_role and node.model_role not in self._roles:
                    if node.model_role not in missing:
                        missing.append(node.model_role)

        return missing

    def list_roles(self) -> dict[str, ModelRoleConfig]:
        """Return all registered roles."""
        return dict(self._roles)

    def register_role(self, config: ModelRoleConfig) -> None:
        """Register or update a model role configuration."""
        self._roles[config.role_id] = config
        logger.info(f"Registered model role: {config.role_id} -> {config.provider}/{config.model}")

    def __len__(self) -> int:
        return len(self._roles)

    def __contains__(self, role_id: str) -> bool:
        return role_id in self._roles
