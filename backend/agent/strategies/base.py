"""Base strategy classes and interfaces for the modular agent strategy framework.

This module defines the abstract base class and data models that all strategies
must implement. Strategies can be domain-specific (legal, HR, software dev) or
general-purpose (legacy, enhanced).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field
from enum import Enum

if TYPE_CHECKING:
    from backend.agent.schemas import WorkerResult, ResultQuality


class StrategyDomain(str, Enum):
    """Domains that strategies can be optimized for."""
    GENERAL = "general"
    SOFTWARE_DEV = "software_dev"
    LEGAL = "legal"
    HR = "hr"
    FINANCE = "finance"
    RESEARCH = "research"


class StrategyMetadata(BaseModel):
    """Metadata for strategy registration and selection."""
    id: str = Field(description="Unique identifier for the strategy")
    name: str = Field(description="Human-readable display name")
    version: str = Field(description="Version string (semver)")
    description: str = Field(description="What this strategy is optimized for")
    domains: List[StrategyDomain] = Field(
        default_factory=lambda: [StrategyDomain.GENERAL],
        description="Domains this strategy is suitable for"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Searchable tags: fast, thorough, citation-heavy, etc."
    )
    author: str = Field(default="system", description="Strategy author")
    is_default: bool = Field(default=False, description="Use as default strategy")
    is_legacy: bool = Field(default=False, description="Marks legacy/baseline strategy")
    
    class Config:
        use_enum_values = True


class StrategyConfig(BaseModel):
    """Runtime configuration for a strategy instance."""
    # Iteration control
    max_iterations: int = Field(default=3, description="Maximum orchestrator iterations")
    confidence_threshold: float = Field(
        default=0.8, 
        ge=0.0, le=1.0,
        description="Confidence level to trigger early exit"
    )
    early_exit_enabled: bool = Field(default=True, description="Allow early exit on high confidence")
    
    # Search scoring
    cross_search_boost: float = Field(
        default=1.15,
        description="Score multiplier for docs found in both vector and text search"
    )
    content_length_penalty: bool = Field(
        default=True,
        description="Penalize very short content chunks"
    )
    min_content_length: int = Field(default=50, description="Minimum content length before penalty")
    
    # Response generation
    require_citations: bool = Field(default=True, description="Require source citations in response")
    max_sources_cited: int = Field(default=10, description="Maximum sources to cite")
    
    # Strategy-specific overrides
    custom_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific configuration parameters"
    )
    
    class Config:
        extra = "allow"


class BaseStrategy(ABC):
    """Abstract base class for all agent strategies.
    
    Each strategy defines:
    - Prompts for each orchestrator phase (analyze, plan, evaluate, synthesize)
    - Post-processing logic for analysis results
    - Early exit conditions
    - RRF scoring modifications
    
    Strategies are registered with the StrategyRegistry and can be selected
    at runtime based on domain, user preference, or automatic detection.
    """
    
    def __init__(self, config: Optional[StrategyConfig] = None):
        """Initialize strategy with configuration.
        
        Args:
            config: Optional runtime configuration overrides
        """
        self._config = config or StrategyConfig()
    
    @property
    @abstractmethod
    def metadata(self) -> StrategyMetadata:
        """Return strategy metadata for registration."""
        pass
    
    @property
    def config(self) -> StrategyConfig:
        """Return strategy configuration."""
        return self._config
    
    @config.setter
    def config(self, value: StrategyConfig):
        """Set strategy configuration."""
        self._config = value
    
    # ============== Prompt Methods ==============
    
    @abstractmethod
    def get_analyze_prompt(self) -> str:
        """Return the analysis phase prompt template.
        
        The prompt should accept these format variables:
        - {user_message}: The user's query
        - {conversation_history}: Previous conversation context
        
        Returns:
            Prompt template string
        """
        pass
    
    @abstractmethod
    def get_plan_prompt(self) -> str:
        """Return the planning phase prompt template.
        
        The prompt should accept these format variables:
        - {analysis}: JSON analysis from analyze phase
        - {available_sources}: List of available data sources
        
        Returns:
            Prompt template string
        """
        pass
    
    @abstractmethod
    def get_evaluate_prompt(self) -> str:
        """Return the evaluation phase prompt template.
        
        The prompt should accept these format variables:
        - {intent}: Original user intent
        - {success_criteria}: What constitutes success
        - {results_summary}: Summary of worker results
        
        Returns:
            Prompt template string
        """
        pass
    
    @abstractmethod
    def get_synthesize_prompt(self) -> str:
        """Return the synthesis phase prompt template.
        
        The prompt should accept these format variables:
        - {user_message}: Original user query
        - {all_results}: All gathered information
        
        Returns:
            Prompt template string
        """
        pass
    
    def get_fast_response_prompt(self) -> str:
        """Return the fast response prompt for non-orchestrated responses.
        
        The prompt should accept these format variables:
        - {user_message}: The user's query
        - {context}: Search results context
        
        Returns:
            Prompt template string
        """
        # Default implementation - can be overridden
        return """Based on the following information, answer the user's question directly.

**User Question:**
{user_message}

**Available Information:**
{context}

**Response Requirements:**
1. Start with the answer - Don't begin with "Based on..." or "According to..."
2. Cite every fact - Format: [Source: "Document Title"]
3. Be concise but complete
4. Acknowledge gaps if information is incomplete

Generate your response now:"""
    
    # ============== Processing Methods ==============
    
    @abstractmethod
    def process_analysis(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        """Post-process the raw analysis result from the LLM.
        
        This method can:
        - Extract and normalize entities
        - Validate required fields
        - Apply strategy-specific transformations
        
        Args:
            raw_result: Raw JSON result from LLM
        
        Returns:
            Processed analysis dict
        """
        pass
    
    def process_plan(self, raw_result: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Post-process the raw plan result from the LLM.
        
        Args:
            raw_result: Raw JSON result from LLM
            analysis: The analysis result
        
        Returns:
            Processed plan dict
        """
        # Default implementation - just return raw result
        return raw_result
    
    def process_evaluation(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        """Post-process the raw evaluation result from the LLM.
        
        Args:
            raw_result: Raw JSON result from LLM
        
        Returns:
            Processed evaluation dict
        """
        # Default implementation - just return raw result
        return raw_result
    
    # ============== Decision Methods ==============
    
    @abstractmethod
    def should_early_exit(
        self,
        results: List['WorkerResult'],
        evaluation: Optional[Dict[str, Any]] = None,
        iteration: int = 1
    ) -> bool:
        """Determine if the agent should exit early based on results quality.
        
        Args:
            results: List of worker results from current iteration
            evaluation: Optional evaluation result from orchestrator
            iteration: Current iteration number
        
        Returns:
            True if should exit early, False to continue
        """
        pass
    
    def get_result_quality_threshold(self) -> float:
        """Return the minimum average score for 'good' quality results.
        
        Returns:
            Score threshold (0.0 - 1.0)
        """
        return 0.7
    
    # ============== Scoring Methods ==============
    
    @abstractmethod
    def calculate_rrf_scores(
        self,
        results: List[Dict[str, Any]],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Apply strategy-specific RRF scoring to search results.
        
        This method can customize:
        - Cross-search boosting
        - Content length penalties
        - Domain-specific relevance adjustments
        
        Args:
            results: Combined vector and text search results
            limit: Maximum results to return
        
        Returns:
            Scored and sorted results list
        """
        pass
    
    # ============== Utility Methods ==============
    
    def get_domain_keywords(self) -> List[str]:
        """Return domain-specific keywords for query detection.
        
        Used by auto-detection to identify when this strategy is appropriate.
        
        Returns:
            List of keywords/phrases
        """
        return []
    
    def matches_query(self, query: str) -> float:
        """Calculate how well this strategy matches a query.
        
        Args:
            query: User's query string
        
        Returns:
            Match score (0.0 - 1.0), higher is better match
        """
        if not self.get_domain_keywords():
            return 0.5  # Neutral for general strategies
        
        query_lower = query.lower()
        keywords = self.get_domain_keywords()
        matches = sum(1 for kw in keywords if kw.lower() in query_lower)
        
        return min(1.0, matches / max(len(keywords) * 0.3, 1))
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize strategy info to dictionary.
        
        Returns:
            Dict with metadata and config
        """
        return {
            "metadata": self.metadata.model_dump(),
            "config": self.config.model_dump()
        }
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(id={self.metadata.id}, version={self.metadata.version})>"
