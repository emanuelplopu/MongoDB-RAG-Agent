"""Multi-provider LLM abstraction layer.

Supports:
- OpenAI (GPT-4, GPT-4o, etc.)
- Google Gemini (gemini-pro, gemini-flash, etc.)
- Anthropic Claude (claude-3-opus, claude-3-sonnet, etc.)
- Local/Offline providers (Ollama, LM Studio, etc.)

Uses LiteLLM for unified interface with provider-specific optimizations.
"""

import logging
from typing import Optional, Dict, Any, List
from litellm import acompletion
from backend.core.model_versions import get_model_parameter_mapping, is_model_compatible_with_parameter

import logging
from typing import Optional, Dict, Any, List, Literal
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    GOOGLE = "google"  # Gemini
    ANTHROPIC = "anthropic"  # Claude
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"  # LM Studio, vLLM, etc.


@dataclass
class LLMConfig:
    """Configuration for a single LLM endpoint."""
    provider: LLMProvider
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    
    # Optional overrides
    temperature: float = 0.7
    max_tokens: int = 2000
    
    # Provider-specific settings
    extra_params: Dict[str, Any] = field(default_factory=dict)
    
    def get_litellm_model(self) -> str:
        """Get the model string in LiteLLM format.
        
        LiteLLM uses prefixes for different providers:
        - OpenAI: model_name (no prefix)
        - Gemini: gemini/model_name
        - Claude: anthropic/model_name or claude-3-xxx
        - Ollama: ollama/model_name
        """
        if self.provider == LLMProvider.OPENAI:
            return self.model
        elif self.provider == LLMProvider.GOOGLE:
            # Gemini models: gemini-pro, gemini-1.5-flash, etc.
            if not self.model.startswith("gemini/"):
                return f"gemini/{self.model}"
            return self.model
        elif self.provider == LLMProvider.ANTHROPIC:
            # Claude models: claude-3-opus, claude-3-sonnet, etc.
            if not self.model.startswith("anthropic/"):
                return f"anthropic/{self.model}"
            return self.model
        elif self.provider == LLMProvider.OLLAMA:
            if not self.model.startswith("ollama/"):
                return f"ollama/{self.model}"
            return self.model
        elif self.provider == LLMProvider.OPENAI_COMPATIBLE:
            # Custom endpoint - use openai/ prefix with base_url
            if not self.model.startswith("openai/"):
                return f"openai/{self.model}"
            return self.model
        return self.model
    
    def get_api_key_param(self) -> Dict[str, str]:
        """Get the API key parameter for LiteLLM."""
        if not self.api_key:
            return {}
        
        # LiteLLM uses api_key for all providers
        return {"api_key": self.api_key}
    
    def get_base_url_param(self) -> Dict[str, str]:
        """Get the base URL parameter for LiteLLM."""
        if not self.base_url:
            return {}
        
        return {"base_url": self.base_url}


@dataclass 
class DualLLMConfig:
    """Configuration for the dual-LLM system (orchestrator + worker).
    
    The orchestrator (thinking) model is used for:
    - Analyzing user intent
    - Planning search strategies
    - Evaluating results
    - Synthesizing final answers
    
    The worker (fast) model is used for:
    - Executing searches
    - Summarizing results
    - Quick responses
    """
    orchestrator: LLMConfig  # Thinking/planning model (e.g., GPT-4, Claude-3-Opus)
    worker: LLMConfig  # Fast execution model (e.g., Gemini Flash, GPT-4o-mini)
    
    # Embedding configuration (separate from chat models)
    embedding: Optional[LLMConfig] = None


class LLMClient:
    """Unified LLM client using LiteLLM for multi-provider support."""
    
    def __init__(self, config: LLMConfig):
        """Initialize LLM client with configuration.
        
        Args:
            config: LLM configuration
        """
        self.config = config
        self._model = config.get_litellm_model()
    
    async def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate a completion.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Override default temperature
            max_tokens: Override default max tokens
            **kwargs: Additional parameters passed to LiteLLM
        
        Returns:
            Generated text content
        """
        from litellm import acompletion
        
        params = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            **self.config.get_api_key_param(),
            **self.config.get_base_url_param(),
            **self.config.extra_params,
            **kwargs
        }
        
        # Handle parameter adaptation based on model compatibility
        param_mapping = get_model_parameter_mapping(self._model)
        
        # Handle max_tokens vs max_completion_tokens
        if max_tokens is not None:
            if "max_tokens" in param_mapping:
                mapped_param = param_mapping["max_tokens"]
                params[mapped_param] = max_tokens
                if mapped_param != "max_tokens":
                    # Remove original if it was mapped
                    params.pop("max_tokens", None)
            else:
                params["max_tokens"] = max_tokens
        else:
            params["max_tokens"] = self.config.max_tokens
        
        try:
            response = await acompletion(**params)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM completion failed for {self._model}: {e}")
            raise
    
    async def complete_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate a completion and parse as JSON.
        
        Args:
            messages: List of message dicts
            temperature: Override default temperature
            max_tokens: Override default max tokens
            **kwargs: Additional parameters
        
        Returns:
            Parsed JSON response
        """
        import json
        
        content = await self.complete(messages, temperature, max_tokens, **kwargs)
        
        # Try to parse JSON, handling markdown code blocks
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response: {content[:200]}")
            return {"response": content, "parse_error": str(e)}


class LLMProviderManager:
    """Manager for LLM providers with configuration from database."""
    
    # Default model recommendations per provider
    RECOMMENDED_MODELS = {
        LLMProvider.OPENAI: {
            "orchestrator": ["gpt-4o", "gpt-4-turbo", "gpt-4"],
            "worker": ["gpt-4o-mini", "gpt-3.5-turbo"],
            "embedding": ["text-embedding-3-large", "text-embedding-3-small"]
        },
        LLMProvider.GOOGLE: {
            "orchestrator": ["gemini-1.5-pro", "gemini-pro"],
            "worker": ["gemini-2.0-flash-exp", "gemini-1.5-flash"],
            "embedding": ["text-embedding-004", "embedding-001"]
        },
        LLMProvider.ANTHROPIC: {
            "orchestrator": ["claude-3-opus-20240229", "claude-3-sonnet-20240229"],
            "worker": ["claude-3-haiku-20240307", "claude-3-5-sonnet-20241022"],
            "embedding": []  # Claude doesn't have embedding models
        },
        LLMProvider.OLLAMA: {
            "orchestrator": ["llama3.1:70b", "mixtral:8x7b", "qwen2.5:32b"],
            "worker": ["llama3.1:8b", "mistral:7b", "qwen2.5:7b"],
            "embedding": ["nomic-embed-text", "mxbai-embed-large"]
        }
    }
    
    def __init__(self, db=None):
        """Initialize provider manager.
        
        Args:
            db: Database connection for loading saved config
        """
        self.db = db
        self._config_cache: Optional[DualLLMConfig] = None
    
    async def get_config(self) -> DualLLMConfig:
        """Get the current LLM configuration.
        
        Loads from database if available, otherwise uses defaults.
        """
        if self._config_cache:
            return self._config_cache
        
        if self.db:
            config = await self._load_from_db()
            if config:
                self._config_cache = config
                return config
        
        # Return default configuration
        return self._get_default_config()
    
    async def save_config(self, config: DualLLMConfig) -> bool:
        """Save LLM configuration to database.
        
        Args:
            config: Configuration to save
        
        Returns:
            True if saved successfully
        """
        if not self.db:
            return False
        
        try:
            collection = self.db.db["llm_config"]
            doc = {
                "_id": "active_config",
                "orchestrator": {
                    "provider": config.orchestrator.provider.value,
                    "model": config.orchestrator.model,
                    "api_key": config.orchestrator.api_key,
                    "base_url": config.orchestrator.base_url,
                    "temperature": config.orchestrator.temperature,
                    "max_tokens": config.orchestrator.max_tokens,
                },
                "worker": {
                    "provider": config.worker.provider.value,
                    "model": config.worker.model,
                    "api_key": config.worker.api_key,
                    "base_url": config.worker.base_url,
                    "temperature": config.worker.temperature,
                    "max_tokens": config.worker.max_tokens,
                },
            }
            
            if config.embedding:
                doc["embedding"] = {
                    "provider": config.embedding.provider.value,
                    "model": config.embedding.model,
                    "api_key": config.embedding.api_key,
                    "base_url": config.embedding.base_url,
                }
            
            await collection.replace_one({"_id": "active_config"}, doc, upsert=True)
            self._config_cache = config
            return True
        except Exception as e:
            logger.error(f"Failed to save LLM config: {e}")
            return False
    
    async def _load_from_db(self) -> Optional[DualLLMConfig]:
        """Load configuration from database."""
        try:
            collection = self.db.db["llm_config"]
            doc = await collection.find_one({"_id": "active_config"})
            
            if not doc:
                return None
            
            orchestrator = LLMConfig(
                provider=LLMProvider(doc["orchestrator"]["provider"]),
                model=doc["orchestrator"]["model"],
                api_key=doc["orchestrator"].get("api_key"),
                base_url=doc["orchestrator"].get("base_url"),
                temperature=doc["orchestrator"].get("temperature", 0.2),
                max_tokens=doc["orchestrator"].get("max_tokens", 2000),
            )
            
            worker = LLMConfig(
                provider=LLMProvider(doc["worker"]["provider"]),
                model=doc["worker"]["model"],
                api_key=doc["worker"].get("api_key"),
                base_url=doc["worker"].get("base_url"),
                temperature=doc["worker"].get("temperature", 0.7),
                max_tokens=doc["worker"].get("max_tokens", 1500),
            )
            
            embedding = None
            if "embedding" in doc:
                embedding = LLMConfig(
                    provider=LLMProvider(doc["embedding"]["provider"]),
                    model=doc["embedding"]["model"],
                    api_key=doc["embedding"].get("api_key"),
                    base_url=doc["embedding"].get("base_url"),
                )
            
            return DualLLMConfig(orchestrator=orchestrator, worker=worker, embedding=embedding)
        except Exception as e:
            logger.error(f"Failed to load LLM config from database: {e}")
            return None
    
    def _get_default_config(self) -> DualLLMConfig:
        """Get default configuration from environment/settings."""
        from backend.core.config import settings
        
        # Determine provider from settings
        provider = LLMProvider.OPENAI
        if "gemini" in settings.llm_model.lower():
            provider = LLMProvider.GOOGLE
        elif "claude" in settings.llm_model.lower():
            provider = LLMProvider.ANTHROPIC
        elif "ollama" in settings.llm_model.lower():
            provider = LLMProvider.OLLAMA
        
        orchestrator = LLMConfig(
            provider=provider,
            model=settings.orchestrator_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url if settings.llm_base_url != "https://api.openai.com/v1" else None,
            temperature=0.2,
            max_tokens=2000,
        )
        
        # Worker might use different provider
        worker_provider = LLMProvider.OPENAI
        if "gemini" in settings.worker_model.lower():
            worker_provider = LLMProvider.GOOGLE
        elif "claude" in settings.worker_model.lower():
            worker_provider = LLMProvider.ANTHROPIC
        elif "ollama" in settings.worker_model.lower():
            worker_provider = LLMProvider.OLLAMA
        
        worker = LLMConfig(
            provider=worker_provider,
            model=settings.worker_model.replace("gemini/", "").replace("anthropic/", ""),
            api_key=settings.llm_api_key,  # Default to same key
            temperature=0.7,
            max_tokens=1500,
        )
        
        embedding = LLMConfig(
            provider=LLMProvider(settings.embedding_provider) if settings.embedding_provider in [p.value for p in LLMProvider] else LLMProvider.OPENAI,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key or settings.llm_api_key,
            base_url=settings.embedding_base_url if settings.embedding_base_url != "https://api.openai.com/v1" else None,
        )
        
        return DualLLMConfig(orchestrator=orchestrator, worker=worker, embedding=embedding)
    
    def get_orchestrator_client(self, config: Optional[DualLLMConfig] = None) -> LLMClient:
        """Get LLM client for orchestrator (thinking model)."""
        cfg = config or self._config_cache or self._get_default_config()
        return LLMClient(cfg.orchestrator)
    
    def get_worker_client(self, config: Optional[DualLLMConfig] = None) -> LLMClient:
        """Get LLM client for worker (fast model)."""
        cfg = config or self._config_cache or self._get_default_config()
        return LLMClient(cfg.worker)
    
    def invalidate_cache(self):
        """Invalidate the config cache to force reload."""
        self._config_cache = None


# Singleton instance
_provider_manager: Optional[LLMProviderManager] = None


def get_llm_manager(db=None) -> LLMProviderManager:
    """Get the LLM provider manager singleton."""
    global _provider_manager
    if _provider_manager is None:
        _provider_manager = LLMProviderManager(db)
    elif db and _provider_manager.db is None:
        _provider_manager.db = db
    return _provider_manager
