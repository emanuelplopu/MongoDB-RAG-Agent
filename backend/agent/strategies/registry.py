"""Strategy Registry - Central catalog for all available strategies.

The registry provides:
- Strategy registration via decorator
- Strategy lookup by ID or domain
- Default strategy management
- Strategy auto-detection based on query
"""

import logging
from typing import Dict, Type, List, Optional, Any
from functools import wraps

from backend.agent.strategies.base import (
    BaseStrategy, 
    StrategyMetadata, 
    StrategyConfig,
    StrategyDomain
)

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Central registry for all available agent strategies.
    
    Strategies register themselves using the @register decorator,
    and can be retrieved by ID, domain, or auto-detected based on query.
    
    Usage:
        @StrategyRegistry.register
        class MyStrategy(BaseStrategy):
            ...
        
        strategy = StrategyRegistry.get("my_strategy")
        strategies = StrategyRegistry.list_strategies(domain="legal")
    """
    
    _strategies: Dict[str, Type[BaseStrategy]] = {}
    _instances: Dict[str, BaseStrategy] = {}
    _default_id: Optional[str] = None
    
    @classmethod
    def register(cls, strategy_class: Type[BaseStrategy]) -> Type[BaseStrategy]:
        """Decorator to register a strategy class.
        
        Args:
            strategy_class: The strategy class to register
        
        Returns:
            The same class (for decorator chaining)
        
        Raises:
            ValueError: If strategy class doesn't implement metadata properly
            TypeError: If strategy_class is not a valid class
        
        Example:
            @StrategyRegistry.register
            class MyStrategy(BaseStrategy):
                @property
                def metadata(self):
                    return StrategyMetadata(id="my_strategy", ...)
        """
        # Validate input
        if not isinstance(strategy_class, type):
            raise TypeError(f"Expected a class, got {type(strategy_class).__name__}")
        
        if not issubclass(strategy_class, BaseStrategy):
            raise TypeError(f"Strategy class must inherit from BaseStrategy")
        
        # Create a temporary instance to get metadata
        try:
            temp_instance = strategy_class()
            metadata = temp_instance.metadata
        except TypeError as e:
            # Constructor requires arguments we can't provide
            logger.error(f"Failed to instantiate {strategy_class.__name__}: {e}")
            raise ValueError(
                f"Strategy class must have a no-argument constructor or use default values: {e}"
            )
        except AttributeError as e:
            logger.error(f"Strategy {strategy_class.__name__} missing metadata: {e}")
            raise ValueError(f"Strategy class must implement 'metadata' property: {e}")
        except Exception as e:
            logger.error(f"Failed to register strategy {strategy_class.__name__}: {e}")
            raise ValueError(f"Strategy registration failed: {e}")
        
        # Validate metadata
        if not metadata or not metadata.id:
            raise ValueError(f"Strategy metadata must have a valid 'id' field")
        
        strategy_id = metadata.id
        
        if strategy_id in cls._strategies:
            logger.warning(f"Strategy '{strategy_id}' already registered, overwriting")
        
        cls._strategies[strategy_id] = strategy_class
        
        # Track default strategy
        if metadata.is_default:
            if cls._default_id and cls._default_id != strategy_id:
                logger.warning(
                    f"Changing default strategy from '{cls._default_id}' to '{strategy_id}'"
                )
            cls._default_id = strategy_id
        
        logger.info(f"Registered strategy: {strategy_id} (v{metadata.version})")
        
        return strategy_class
    
    @classmethod
    def get(
        cls, 
        strategy_id: str, 
        config: Optional[StrategyConfig] = None,
        cached: bool = True
    ) -> BaseStrategy:
        """Get a strategy instance by ID.
        
        Args:
            strategy_id: The strategy identifier
            config: Optional configuration overrides
            cached: Whether to use cached instance (default True)
        
        Returns:
            Strategy instance
        
        Raises:
            KeyError: If strategy not found
            RuntimeError: If strategy instantiation fails
        """
        if not strategy_id:
            raise KeyError("Strategy ID cannot be empty")
        
        if strategy_id not in cls._strategies:
            available = list(cls._strategies.keys())
            raise KeyError(
                f"Strategy '{strategy_id}' not found. Available: {available}"
            )
        
        # Generate cache key - only cache if no custom config
        use_cache = cached and config is None
        cache_key = f"{strategy_id}:default"
        
        if use_cache and cache_key in cls._instances:
            return cls._instances[cache_key]
        
        # Create new instance
        try:
            strategy_class = cls._strategies[strategy_id]
            instance = strategy_class(config=config)
        except Exception as e:
            logger.error(f"Failed to instantiate strategy '{strategy_id}': {e}")
            raise RuntimeError(f"Strategy instantiation failed: {e}")
        
        # Cache if using default config
        if use_cache:
            cls._instances[cache_key] = instance
        
        return instance
    
    @classmethod
    def get_default(cls, config: Optional[StrategyConfig] = None) -> BaseStrategy:
        """Get the default strategy.
        
        Args:
            config: Optional configuration overrides
        
        Returns:
            Default strategy instance
        
        Raises:
            ValueError: If no strategies are registered
        """
        # Check if any strategies are registered
        if not cls._strategies:
            raise ValueError(
                "No strategies registered. Ensure strategy modules are imported."
            )
        
        # Use explicit default if set
        if cls._default_id is not None:
            try:
                return cls.get(cls._default_id, config)
            except (KeyError, RuntimeError) as e:
                logger.warning(f"Default strategy '{cls._default_id}' failed: {e}")
                # Fall through to find another strategy
        
        # Find first non-legacy strategy
        for sid, strategy_class in cls._strategies.items():
            try:
                temp = strategy_class()
                if not temp.metadata.is_legacy:
                    return cls.get(sid, config)
            except Exception as e:
                logger.warning(f"Strategy '{sid}' instantiation failed: {e}")
                continue
        
        # Fall back to any working strategy
        for sid in cls._strategies.keys():
            try:
                return cls.get(sid, config)
            except Exception as e:
                logger.warning(f"Strategy '{sid}' fallback failed: {e}")
                continue
        
        raise ValueError("No working strategies available")
    
    @classmethod
    def get_for_domain(
        cls, 
        domain: StrategyDomain,
        config: Optional[StrategyConfig] = None
    ) -> BaseStrategy:
        """Get the best strategy for a specific domain.
        
        Args:
            domain: The target domain
            config: Optional configuration overrides
        
        Returns:
            Best matching strategy for the domain
        
        Raises:
            ValueError: If no strategies are available
        """
        if not cls._strategies:
            raise ValueError("No strategies registered")
        
        domain_strategies = []
        
        for strategy_id, strategy_class in cls._strategies.items():
            try:
                temp = strategy_class()
                # Check if domain matches or strategy is general-purpose
                if domain in temp.metadata.domains or StrategyDomain.GENERAL in temp.metadata.domains:
                    # Prioritize domain-specific over general
                    priority = 0 if domain in temp.metadata.domains else 1
                    domain_strategies.append((priority, strategy_id, temp.metadata))
            except Exception as e:
                logger.warning(f"Failed to check domain for strategy '{strategy_id}': {e}")
                continue
        
        if not domain_strategies:
            logger.info(f"No strategies found for domain '{domain}', using default")
            return cls.get_default(config)
        
        # Sort by priority (domain-specific first), then by version
        domain_strategies.sort(key=lambda x: (x[0], x[2].version), reverse=True)
        
        best_id = domain_strategies[0][1]
        return cls.get(best_id, config)
    
    @classmethod
    def auto_detect(
        cls, 
        query: str,
        config: Optional[StrategyConfig] = None
    ) -> BaseStrategy:
        """Auto-detect the best strategy for a query.
        
        Evaluates all registered strategies and returns the one
        with the highest match score for the query.
        
        Args:
            query: User's query string
            config: Optional configuration overrides
        
        Returns:
            Best matching strategy
        
        Raises:
            ValueError: If no strategies are available
        """
        if not cls._strategies:
            raise ValueError("No strategies registered")
        
        if not query or not query.strip():
            logger.debug("Empty query provided, returning default strategy")
            return cls.get_default(config)
        
        best_score = -1.0
        best_id = None
        
        for strategy_id, strategy_class in cls._strategies.items():
            try:
                temp = strategy_class()
                
                # Skip legacy for auto-detection unless it's the only option
                if temp.metadata.is_legacy and len(cls._strategies) > 1:
                    continue
                
                score = temp.matches_query(query)
                
                # Validate score is in valid range
                score = max(0.0, min(1.0, float(score)))
                
                # Boost default strategy slightly
                if strategy_id == cls._default_id:
                    score += 0.1
                
                if score > best_score:
                    best_score = score
                    best_id = strategy_id
                    
            except Exception as e:
                logger.warning(f"Auto-detect failed for strategy '{strategy_id}': {e}")
                continue
        
        if best_id is None:
            logger.debug("No matching strategy found, using default")
            return cls.get_default(config)
        
        logger.debug(f"Auto-detected strategy '{best_id}' for query (score={best_score:.2f})")
        return cls.get(best_id, config)
    
    @classmethod
    def list_strategies(
        cls, 
        domain: Optional[StrategyDomain] = None,
        include_legacy: bool = True
    ) -> List[StrategyMetadata]:
        """List available strategies, optionally filtered.
        
        Args:
            domain: Filter by domain (optional)
            include_legacy: Whether to include legacy strategies
        
        Returns:
            List of strategy metadata
        """
        result = []
        
        for strategy_id, strategy_class in cls._strategies.items():
            temp = strategy_class()
            metadata = temp.metadata
            
            # Apply filters
            if not include_legacy and metadata.is_legacy:
                continue
            
            if domain and domain not in metadata.domains:
                continue
            
            result.append(metadata)
        
        # Sort by name
        result.sort(key=lambda m: m.name)
        
        return result
    
    @classmethod
    def get_strategy_info(cls, strategy_id: str) -> Dict[str, Any]:
        """Get detailed information about a strategy.
        
        Args:
            strategy_id: The strategy identifier
        
        Returns:
            Dict with metadata and configuration
        """
        strategy = cls.get(strategy_id)
        return strategy.to_dict()
    
    @classmethod
    def is_registered(cls, strategy_id: str) -> bool:
        """Check if a strategy is registered.
        
        Args:
            strategy_id: The strategy identifier
        
        Returns:
            True if registered
        """
        return strategy_id in cls._strategies
    
    @classmethod
    def unregister(cls, strategy_id: str) -> bool:
        """Unregister a strategy.
        
        Args:
            strategy_id: The strategy identifier
        
        Returns:
            True if unregistered, False if not found
        """
        if strategy_id not in cls._strategies:
            return False
        
        del cls._strategies[strategy_id]
        
        # Clear cached instances
        to_remove = [k for k in cls._instances if k.startswith(f"{strategy_id}:")]
        for key in to_remove:
            del cls._instances[key]
        
        # Update default if needed
        if cls._default_id == strategy_id:
            cls._default_id = None
        
        logger.info(f"Unregistered strategy: {strategy_id}")
        return True
    
    @classmethod
    def clear(cls):
        """Clear all registered strategies. Use with caution."""
        cls._strategies.clear()
        cls._instances.clear()
        cls._default_id = None
        logger.warning("Cleared all registered strategies")
    
    @classmethod
    def count(cls) -> int:
        """Return number of registered strategies."""
        return len(cls._strategies)


# Convenience function for decorator usage
def register_strategy(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
    """Decorator to register a strategy class.
    
    Alias for StrategyRegistry.register for cleaner imports.
    
    Example:
        from backend.agent.strategies.registry import register_strategy
        
        @register_strategy
        class MyStrategy(BaseStrategy):
            ...
    """
    return StrategyRegistry.register(cls)
