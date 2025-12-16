"""Backend configuration settings."""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List
import os


class BackendSettings(BaseSettings):
    """API server configuration."""
    
    # API Settings
    api_port: int = Field(default=8000, description="API server port")
    api_workers: int = Field(default=4, description="Number of API workers")
    debug: bool = Field(default=False, description="Debug mode")
    
    # CORS Settings
    cors_origins: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000"],
        description="Allowed CORS origins"
    )
    
    # MongoDB Settings (from main settings)
    mongodb_uri: str = Field(
        default="mongodb://localhost:27017/?directConnection=true",
        description="MongoDB connection string"
    )
    mongodb_database: str = Field(default="rag_db", description="Database name")
    mongodb_collection_documents: str = Field(default="documents")
    mongodb_collection_chunks: str = Field(default="chunks")
    mongodb_vector_index: str = Field(default="vector_index")
    mongodb_text_index: str = Field(default="text_index")
    
    # LLM Settings
    llm_provider: str = Field(default="openai")
    llm_api_key: str = Field(default="")
    llm_model: str = Field(default="gpt-4o")
    llm_base_url: str = Field(default="https://api.openai.com/v1")
    
    # Embedding Settings
    embedding_provider: str = Field(default="openai")
    embedding_api_key: str = Field(default="")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_base_url: str = Field(default="https://api.openai.com/v1")
    embedding_dimension: int = Field(default=1536)
    
    # Search Settings
    default_match_count: int = Field(default=10)
    max_match_count: int = Field(default=50)
    default_text_weight: float = Field(default=0.3)
    
    # Profile Settings
    profiles_path: str = Field(default="profiles.yaml")
    
    # Airbyte Integration Settings
    airbyte_enabled: bool = Field(
        default=False, 
        description="Enable Airbyte integration for Confluence/Jira"
    )
    airbyte_api_url: str = Field(
        default="http://airbyte-server:8001",
        description="Airbyte API URL (internal container network)"
    )
    airbyte_webapp_url: str = Field(
        default="http://localhost:11020",
        description="Airbyte Webapp URL (for external access)"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# Load settings - try to use main settings if available
def get_settings() -> BackendSettings:
    """Get backend settings, integrating with main app settings."""
    try:
        from src.settings import load_settings as load_main_settings
        main_settings = load_main_settings()
        
        # Create backend settings with main settings values
        return BackendSettings(
            mongodb_uri=main_settings.mongodb_uri,
            mongodb_database=main_settings.mongodb_database,
            mongodb_collection_documents=main_settings.mongodb_collection_documents,
            mongodb_collection_chunks=main_settings.mongodb_collection_chunks,
            mongodb_vector_index=main_settings.mongodb_vector_index,
            mongodb_text_index=main_settings.mongodb_text_index,
            llm_provider=main_settings.llm_provider,
            llm_api_key=main_settings.llm_api_key,
            llm_model=main_settings.llm_model,
            llm_base_url=main_settings.llm_base_url or "https://api.openai.com/v1",
            embedding_provider=main_settings.embedding_provider,
            embedding_api_key=main_settings.embedding_api_key,
            embedding_model=main_settings.embedding_model,
            embedding_base_url=main_settings.embedding_base_url or "https://api.openai.com/v1",
            embedding_dimension=main_settings.embedding_dimension,
            default_match_count=main_settings.default_match_count,
            max_match_count=main_settings.max_match_count,
            default_text_weight=main_settings.default_text_weight,
            profiles_path=main_settings.profiles_path,
        )
    except Exception:
        # Fallback to environment variables only
        return BackendSettings()


settings = get_settings()
