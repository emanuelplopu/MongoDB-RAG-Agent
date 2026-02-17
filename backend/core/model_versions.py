"""
Comprehensive Model Version Registry

This module maintains a complete registry of all available model versions
from major LLM providers, including their capabilities, parameters, and compatibility.
"""

from typing import Dict, List, Optional, Any
from enum import Enum
from datetime import datetime
from dataclasses import dataclass, field


class ModelCapability(str, Enum):
    """Model capabilities."""
    TEXT_GENERATION = "text_generation"
    MULTIMODAL = "multimodal"
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"
    REASONING = "reasoning"
    CODE_GENERATION = "code_generation"
    FUNCTION_CALLING = "function_calling"


class ModelType(str, Enum):
    """Model types."""
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    VISION = "vision"
    AUDIO = "audio"


@dataclass
class ModelVersion:
    """Complete model version information."""
    id: str
    name: str
    provider: str
    type: ModelType
    version: str
    release_date: Optional[datetime] = None
    context_window: int = 4096
    max_output_tokens: int = 4096
    capabilities: List[ModelCapability] = field(default_factory=list)
    pricing_input: Optional[float] = None  # USD per 1M tokens
    pricing_output: Optional[float] = None  # USD per 1M tokens
    is_deprecated: bool = False
    is_experimental: bool = False
    requires_api_key: bool = True
    api_endpoint: Optional[str] = None
    parameter_mapping: Dict[str, str] = field(default_factory=dict)
    default_parameters: Dict[str, Any] = field(default_factory=dict)


# ==================== OpenAI Models ====================
OPENAI_MODELS: Dict[str, ModelVersion] = {
    # GPT-5 Series (Latest)
    "gpt-5.2": ModelVersion(
        id="gpt-5.2",
        name="GPT-5.2",
        provider="openai",
        type=ModelType.CHAT,
        version="5.2",
        release_date=datetime(2025, 1, 15),
        context_window=200000,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=3.00,
        pricing_output=10.00,
        parameter_mapping={
            "max_tokens": "max_completion_tokens"
        },
        default_parameters={
            "temperature": 0.7,
            "top_p": 1.0
        }
    ),
    "gpt-5.1": ModelVersion(
        id="gpt-5.1",
        name="GPT-5.1",
        provider="openai",
        type=ModelType.CHAT,
        version="5.1",
        release_date=datetime(2024, 12, 1),
        context_window=200000,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=2.50,
        pricing_output=7.50,
        parameter_mapping={
            "max_tokens": "max_completion_tokens"
        }
    ),
    "gpt-5": ModelVersion(
        id="gpt-5",
        name="GPT-5",
        provider="openai",
        type=ModelType.CHAT,
        version="5.0",
        release_date=datetime(2024, 10, 1),
        context_window=128000,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=1.50,
        pricing_output=5.00,
        parameter_mapping={
            "max_tokens": "max_completion_tokens"
        }
    ),
    
    # GPT-4o Series
    "gpt-4o": ModelVersion(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        type=ModelType.CHAT,
        version="4o",
        release_date=datetime(2024, 5, 13),
        context_window=128000,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=5.00,
        pricing_output=15.00
    ),
    "gpt-4o-mini": ModelVersion(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="openai",
        type=ModelType.CHAT,
        version="4o-mini",
        release_date=datetime(2024, 7, 18),
        context_window=128000,
        max_output_tokens=16384,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=0.150,
        pricing_output=0.600
    ),
    
    # O-Series Reasoning Models
    "o1-preview": ModelVersion(
        id="o1-preview",
        name="O1 Preview",
        provider="openai",
        type=ModelType.CHAT,
        version="o1-preview",
        release_date=datetime(2024, 9, 12),
        context_window=128000,
        max_output_tokens=32768,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=15.00,
        pricing_output=60.00,
        parameter_mapping={
            "max_tokens": "max_completion_tokens"
        }
    ),
    "o1-mini": ModelVersion(
        id="o1-mini",
        name="O1 Mini",
        provider="openai",
        type=ModelType.CHAT,
        version="o1-mini",
        release_date=datetime(2024, 9, 12),
        context_window=128000,
        max_output_tokens=65536,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=3.00,
        pricing_output=12.00,
        parameter_mapping={
            "max_tokens": "max_completion_tokens"
        }
    ),
    
    # Legacy GPT-4 Models
    "gpt-4-turbo": ModelVersion(
        id="gpt-4-turbo",
        name="GPT-4 Turbo",
        provider="openai",
        type=ModelType.CHAT,
        version="4-turbo",
        release_date=datetime(2024, 1, 25),
        context_window=128000,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=10.00,
        pricing_output=30.00,
        is_deprecated=True
    ),
    "gpt-4": ModelVersion(
        id="gpt-4",
        name="GPT-4",
        provider="openai",
        type=ModelType.CHAT,
        version="4",
        release_date=datetime(2023, 3, 14),
        context_window=8192,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=30.00,
        pricing_output=60.00,
        is_deprecated=True
    ),
    
    # GPT-3.5 Models
    "gpt-3.5-turbo": ModelVersion(
        id="gpt-3.5-turbo",
        name="GPT-3.5 Turbo",
        provider="openai",
        type=ModelType.CHAT,
        version="3.5-turbo",
        release_date=datetime(2023, 11, 6),
        context_window=16385,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=0.50,
        pricing_output=1.50
    ),
    
    # Embedding Models
    "text-embedding-3-small": ModelVersion(
        id="text-embedding-3-small",
        name="Text Embedding 3 Small",
        provider="openai",
        type=ModelType.EMBEDDING,
        version="3-small",
        release_date=datetime(2024, 1, 25),
        context_window=8191,
        max_output_tokens=1536,
        capabilities=[ModelCapability.TEXT_GENERATION],
        pricing_input=0.02,
        pricing_output=None
    ),
    "text-embedding-3-large": ModelVersion(
        id="text-embedding-3-large",
        name="Text Embedding 3 Large",
        provider="openai",
        type=ModelType.EMBEDDING,
        version="3-large",
        release_date=datetime(2024, 1, 25),
        context_window=8191,
        max_output_tokens=3072,
        capabilities=[ModelCapability.TEXT_GENERATION],
        pricing_input=0.13,
        pricing_output=None
    ),
}


# ==================== Google Gemini Models ====================
GOOGLE_MODELS: Dict[str, ModelVersion] = {
    "gemini-2.0-flash-exp": ModelVersion(
        id="gemini-2.0-flash-exp",
        name="Gemini 2.0 Flash Experimental",
        provider="google",
        type=ModelType.CHAT,
        version="2.0-flash",
        release_date=datetime(2024, 12, 1),
        context_window=1048576,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.AUDIO_INPUT,
            ModelCapability.AUDIO_OUTPUT,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=0.15,
        pricing_output=0.60
    ),
    "gemini-1.5-flash": ModelVersion(
        id="gemini-1.5-flash",
        name="Gemini 1.5 Flash",
        provider="google",
        type=ModelType.CHAT,
        version="1.5-flash",
        release_date=datetime(2024, 2, 15),
        context_window=1048576,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=0.35,
        pricing_output=1.05
    ),
    "gemini-1.5-pro": ModelVersion(
        id="gemini-1.5-pro",
        name="Gemini 1.5 Pro",
        provider="google",
        type=ModelType.CHAT,
        version="1.5-pro",
        release_date=datetime(2024, 2, 15),
        context_window=2097152,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=1.25,
        pricing_output=5.00
    ),
    "gemini-pro": ModelVersion(
        id="gemini-pro",
        name="Gemini Pro",
        provider="google",
        type=ModelType.CHAT,
        version="pro",
        release_date=datetime(2023, 12, 6),
        context_window=32768,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=0.50,
        pricing_output=1.50,
        is_deprecated=True
    ),
    "text-embedding-004": ModelVersion(
        id="text-embedding-004",
        name="Text Embedding 004",
        provider="google",
        type=ModelType.EMBEDDING,
        version="004",
        release_date=datetime(2024, 3, 1),
        context_window=2048,
        max_output_tokens=768,
        capabilities=[ModelCapability.TEXT_GENERATION],
        pricing_input=0.025,
        pricing_output=None
    ),
}


# ==================== Anthropic Claude Models ====================
ANTHROPIC_MODELS: Dict[str, ModelVersion] = {
    "claude-3-5-sonnet-latest": ModelVersion(
        id="claude-3-5-sonnet-latest",
        name="Claude 3.5 Sonnet (Latest)",
        provider="anthropic",
        type=ModelType.CHAT,
        version="3.5-sonnet",
        release_date=datetime(2024, 10, 22),
        context_window=200000,
        max_output_tokens=8192,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION,
            ModelCapability.FUNCTION_CALLING
        ],
        pricing_input=3.00,
        pricing_output=15.00
    ),
    "claude-3-opus-latest": ModelVersion(
        id="claude-3-opus-latest",
        name="Claude 3 Opus (Latest)",
        provider="anthropic",
        type=ModelType.CHAT,
        version="3-opus",
        release_date=datetime(2024, 3, 4),
        context_window=200000,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.REASONING,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=15.00,
        pricing_output=75.00
    ),
    "claude-3-haiku-20240307": ModelVersion(
        id="claude-3-haiku-20240307",
        name="Claude 3 Haiku",
        provider="anthropic",
        type=ModelType.CHAT,
        version="3-haiku",
        release_date=datetime(2024, 3, 7),
        context_window=200000,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.MULTIMODAL,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=0.25,
        pricing_output=1.25
    ),
    "claude-2.1": ModelVersion(
        id="claude-2.1",
        name="Claude 2.1",
        provider="anthropic",
        type=ModelType.CHAT,
        version="2.1",
        release_date=datetime(2023, 11, 21),
        context_window=200000,
        max_output_tokens=4096,
        capabilities=[
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CODE_GENERATION
        ],
        pricing_input=8.00,
        pricing_output=24.00,
        is_deprecated=True
    ),
}


# ==================== Combined Registry ====================
ALL_MODELS: Dict[str, ModelVersion] = {
    **OPENAI_MODELS,
    **GOOGLE_MODELS,
    **ANTHROPIC_MODELS
}


def get_model_by_id(model_id: str) -> Optional[ModelVersion]:
    """Get model version by ID."""
    return ALL_MODELS.get(model_id)


def get_models_by_provider(provider: str) -> List[ModelVersion]:
    """Get all models for a specific provider."""
    return [model for model in ALL_MODELS.values() if model.provider == provider]


def get_models_by_capability(capability: ModelCapability) -> List[ModelVersion]:
    """Get all models that support a specific capability."""
    return [model for model in ALL_MODELS.values() if capability in model.capabilities]


def get_compatible_models(model_type: ModelType) -> List[ModelVersion]:
    """Get all models of a specific type."""
    return [model for model in ALL_MODELS.values() if model.type == model_type]


def get_latest_models(limit: int = 10) -> List[ModelVersion]:
    """Get the latest released models."""
    sorted_models = sorted(
        ALL_MODELS.values(),
        key=lambda m: m.release_date or datetime.min,
        reverse=True
    )
    return sorted_models[:limit]


def get_cost_effective_models(limit: int = 10) -> List[ModelVersion]:
    """Get the most cost-effective models (lowest combined pricing)."""
    models_with_pricing = [
        model for model in ALL_MODELS.values()
        if model.pricing_input is not None and model.pricing_output is not None
    ]
    sorted_models = sorted(
        models_with_pricing,
        key=lambda m: (m.pricing_input or 0) + (m.pricing_output or 0)
    )
    return sorted_models[:limit]


def get_model_parameter_mapping(model_id: str) -> Dict[str, str]:
    """Get parameter mapping for a specific model."""
    model = get_model_by_id(model_id)
    return model.parameter_mapping if model else {}


def is_model_compatible_with_parameter(model_id: str, param_name: str) -> bool:
    """Check if a model is compatible with a specific parameter."""
    model = get_model_by_id(model_id)
    if not model:
        return False
    
    # Check if parameter needs mapping
    if param_name in model.parameter_mapping:
        return True
    
    # Check default parameters
    if param_name in model.default_parameters:
        return True
    
    # Assume compatibility for standard parameters
    standard_params = ["temperature", "top_p", "frequency_penalty", "presence_penalty"]
    return param_name in standard_params