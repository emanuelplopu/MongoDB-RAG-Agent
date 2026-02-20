"""Modular Strategy Framework for the Agent System.

This package provides a pluggable strategy architecture allowing:
- Domain-specific strategies (legal, HR, software development)
- A/B testing between strategies
- Easy addition of new strategies

Usage:
    from backend.agent.strategies import StrategyRegistry, BaseStrategy
    
    # Get a strategy by ID
    strategy = StrategyRegistry.get("enhanced")
    
    # Auto-detect best strategy for a query
    strategy = StrategyRegistry.auto_detect("How do I file a lawsuit?")
    
    # List available strategies
    strategies = StrategyRegistry.list_strategies(domain="legal")
    
    # Get metrics for A/B testing
    from backend.agent.strategies import get_strategy_metrics
    metrics = get_strategy_metrics()
"""

from backend.agent.strategies.base import (
    BaseStrategy,
    StrategyMetadata,
    StrategyConfig,
    StrategyDomain
)
from backend.agent.strategies.registry import (
    StrategyRegistry,
    register_strategy
)
from backend.agent.strategies.metrics import (
    StrategyMetrics,
    get_strategy_metrics
)

# Import concrete strategies to trigger registration
from backend.agent.strategies import legacy
from backend.agent.strategies import enhanced

# Import domain strategies to trigger registration
from backend.agent.strategies.domains import software_dev, legal, hr

__all__ = [
    "BaseStrategy",
    "StrategyMetadata", 
    "StrategyConfig",
    "StrategyDomain",
    "StrategyRegistry",
    "register_strategy",
    "StrategyMetrics",
    "get_strategy_metrics",
]
