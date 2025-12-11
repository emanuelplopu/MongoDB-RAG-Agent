"""Settings configuration for MongoDB RAG Agent."""

from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict
from dotenv import load_dotenv
from typing import Optional, TYPE_CHECKING
import os

if TYPE_CHECKING:
    from src.profile import ProfileConfig

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = ConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # MongoDB Configuration
    mongodb_uri: str = Field(..., description="MongoDB Atlas connection string")

    mongodb_database: str = Field(default="rag_db", description="MongoDB database name")

    mongodb_collection_documents: str = Field(
        default="documents", description="Collection for source documents"
    )

    mongodb_collection_chunks: str = Field(
        default="chunks", description="Collection for document chunks with embeddings"
    )

    mongodb_vector_index: str = Field(
        default="vector_index",
        description="Vector search index name (must be created in Atlas UI)",
    )

    mongodb_text_index: str = Field(
        default="text_index",
        description="Full-text search index name (must be created in Atlas UI)",
    )

    # Profile configuration
    active_profile: Optional[str] = Field(
        default=None,
        description="Active profile name (overrides profiles.yaml)"
    )

    profiles_path: str = Field(
        default="profiles.yaml",
        description="Path to profiles configuration file"
    )

    # LLM Configuration (OpenAI-compatible)
    llm_provider: str = Field(
        default="openrouter",
        description="LLM provider (openai, anthropic, gemini, ollama, etc.)",
    )

    llm_api_key: str = Field(..., description="API key for the LLM provider")

    llm_model: str = Field(
        default="anthropic/claude-haiku-4.5",
        description="Model to use for search and summarization",
    )

    llm_base_url: Optional[str] = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for the LLM API (for OpenAI-compatible providers)",
    )

    # Embedding Configuration
    embedding_provider: str = Field(default="openai", description="Embedding provider")

    embedding_api_key: str = Field(..., description="API key for embedding provider")

    embedding_model: str = Field(
        default="text-embedding-3-small", description="Embedding model to use"
    )

    embedding_base_url: Optional[str] = Field(
        default="https://api.openai.com/v1", description="Base URL for embedding API"
    )

    embedding_dimension: int = Field(
        default=1536,
        description="Embedding vector dimension (1536 for text-embedding-3-small)",
    )

    # Search Configuration
    default_match_count: int = Field(
        default=10, description="Default number of search results to return"
    )

    max_match_count: int = Field(
        default=50, description="Maximum number of search results allowed"
    )

    default_text_weight: float = Field(
        default=0.3, description="Default text weight for hybrid search (0-1)"
    )

    def apply_profile(self, profile: "ProfileConfig") -> "Settings":
        """
        Apply profile-specific settings overrides.
        
        Args:
            profile: Profile configuration to apply
            
        Returns:
            New Settings instance with profile overrides
        """
        # Create a copy with profile overrides
        overrides = {
            'mongodb_database': profile.database,
            'mongodb_collection_documents': profile.collection_documents,
            'mongodb_collection_chunks': profile.collection_chunks,
            'mongodb_vector_index': profile.vector_index,
            'mongodb_text_index': profile.text_index,
        }
        
        # Apply optional overrides if specified in profile
        if profile.embedding_model:
            overrides['embedding_model'] = profile.embedding_model
        if profile.llm_model:
            overrides['llm_model'] = profile.llm_model
        
        # Create new settings with overrides
        current_data = {
            'mongodb_uri': self.mongodb_uri,
            'mongodb_database': overrides.get('mongodb_database', self.mongodb_database),
            'mongodb_collection_documents': overrides.get('mongodb_collection_documents', self.mongodb_collection_documents),
            'mongodb_collection_chunks': overrides.get('mongodb_collection_chunks', self.mongodb_collection_chunks),
            'mongodb_vector_index': overrides.get('mongodb_vector_index', self.mongodb_vector_index),
            'mongodb_text_index': overrides.get('mongodb_text_index', self.mongodb_text_index),
            'llm_provider': self.llm_provider,
            'llm_api_key': self.llm_api_key,
            'llm_model': overrides.get('llm_model', self.llm_model),
            'llm_base_url': self.llm_base_url,
            'embedding_provider': self.embedding_provider,
            'embedding_api_key': self.embedding_api_key,
            'embedding_model': overrides.get('embedding_model', self.embedding_model),
            'embedding_base_url': self.embedding_base_url,
            'embedding_dimension': self.embedding_dimension,
            'default_match_count': self.default_match_count,
            'max_match_count': self.max_match_count,
            'default_text_weight': self.default_text_weight,
            'active_profile': self.active_profile,
            'profiles_path': self.profiles_path,
        }
        
        return Settings(**current_data)


# Cached settings with profile applied
_settings_cache: Optional[Settings] = None


def load_settings(use_profile: bool = True) -> Settings:
    """
    Load settings with proper error handling and optional profile support.
    
    Args:
        use_profile: Whether to apply active profile settings (default: True)
        
    Returns:
        Settings instance with profile overrides applied if enabled
    """
    global _settings_cache
    
    try:
        base_settings = Settings()
        
        if not use_profile:
            return base_settings
        
        # Apply profile if enabled
        from src.profile import get_profile_manager
        
        profile_manager = get_profile_manager(base_settings.profiles_path)
        
        # Check for environment variable override
        if base_settings.active_profile:
            profile_manager.switch_profile(base_settings.active_profile)
        
        active_profile = profile_manager.active_profile
        
        # Apply profile settings
        return base_settings.apply_profile(active_profile)
        
    except Exception as e:
        error_msg = f"Failed to load settings: {e}"
        if "mongodb_uri" in str(e).lower():
            error_msg += "\nMake sure to set MONGODB_URI in your .env file"
        if "llm_api_key" in str(e).lower():
            error_msg += "\nMake sure to set LLM_API_KEY in your .env file"
        if "embedding_api_key" in str(e).lower():
            error_msg += "\nMake sure to set EMBEDDING_API_KEY in your .env file"
        raise ValueError(error_msg) from e


def get_active_profile_name() -> str:
    """Get the name of the currently active profile."""
    from src.profile import get_profile_manager
    settings = Settings()
    profile_manager = get_profile_manager(settings.profiles_path)
    return profile_manager.active_profile_name
