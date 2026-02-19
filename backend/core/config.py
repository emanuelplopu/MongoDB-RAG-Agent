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
    
    # LLM Settings (Primary/Orchestrator)
    llm_provider: str = Field(default="openai")
    llm_api_key: str = Field(default="")
    llm_model: str = Field(default="gpt-4o")
    llm_base_url: str = Field(default="https://api.openai.com/v1")
    
    # Provider-specific API Keys
    openai_api_key: str = Field(default="", description="OpenAI API key")
    google_api_key: str = Field(default="", description="Google Gemini API key")
    anthropic_api_key: str = Field(default="", description="Anthropic Claude API key")
    
    # Fast/Worker LLM Settings (separate from orchestrator)
    fast_llm_provider: str = Field(default="google", description="Provider for fast/worker model")
    fast_llm_model: str = Field(default="gemini-2.0-flash-exp", description="Fast model name")
    fast_llm_api_key: str = Field(default="", description="API key for fast model (if different)")
    fast_llm_base_url: str = Field(default="", description="Base URL for fast model (if custom)")
    
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
    
    # Agent Settings
    agent_max_tool_iterations: int = Field(
        default=5,
        description="Maximum number of tool calls the agent can make per response"
    )
    agent_mode: str = Field(
        default="auto",
        description="Default agent mode: auto, thinking, or fast"
    )
    
    # Orchestrator Model (thinking/planning model)
    orchestrator_model: str = Field(
        default="gpt-4o",
        description="Model for orchestrator (planning, evaluation, synthesis)"
    )
    orchestrator_provider: str = Field(
        default="openai",
        description="Provider for orchestrator model"
    )
    
    # Worker Model (fast execution model)
    worker_model: str = Field(
        default="gemini-2.0-flash-exp",
        description="Model for workers (search execution, summarization)"
    )
    worker_provider: str = Field(
        default="google",
        description="Provider for worker model"
    )
    
    # Federated Agent Settings
    agent_max_iterations: int = Field(
        default=3,
        description="Maximum orchestrator-worker iterations"
    )
    agent_parallel_workers: int = Field(
        default=4,
        description="Maximum parallel worker tasks per chat session"
    )
    
    # Agent Performance & Pool Settings
    agent_global_max_orchestrators: int = Field(
        default=10,
        description="Maximum concurrent orchestrators across all users"
    )
    agent_global_max_workers: int = Field(
        default=20,
        description="Maximum concurrent workers across all users"
    )
    agent_worker_timeout: int = Field(
        default=60,
        description="Timeout in seconds for individual worker tasks"
    )
    agent_orchestrator_timeout: int = Field(
        default=120,
        description="Timeout in seconds for orchestrator phases"
    )
    agent_total_timeout: int = Field(
        default=300,
        description="Maximum total time in seconds for a complete agent request"
    )
    agent_default_mode: str = Field(
        default="auto",
        description="Default agent mode: auto, thinking, or fast"
    )
    agent_auto_fast_threshold: int = Field(
        default=50,
        description="Query character length below which auto mode uses fast processing"
    )
    agent_skip_evaluation: bool = Field(
        default=False,
        description="Skip evaluation phase for faster processing (less refined results)"
    )
    agent_max_sources_per_search: int = Field(
        default=10,
        description="Maximum number of data sources to search in parallel"
    )
    
    # Ingestion Worker Settings
    ingestion_process_isolation: bool = Field(
        default=True,
        description="Run ingestion in a separate worker process for API responsiveness"
    )
    ingestion_max_concurrent_files: int = Field(
        default=2,
        ge=1, le=10,
        description="Maximum number of files to process concurrently"
    )
    ingestion_embedding_batch_size: int = Field(
        default=100,
        ge=10, le=500,
        description="Number of chunks to embed in a single API call"
    )
    ingestion_thread_pool_workers: int = Field(
        default=4,
        ge=1, le=16,
        description="Number of thread pool workers for CPU-bound tasks"
    )
    ingestion_embedding_requests_per_minute: int = Field(
        default=3000,
        ge=100, le=10000,
        description="Rate limit for embedding API requests"
    )
    ingestion_file_processing_timeout: int = Field(
        default=300,
        ge=60, le=1800,
        description="Timeout in seconds for processing a single file"
    )
    ingestion_job_poll_interval: float = Field(
        default=1.0,
        ge=0.5, le=10.0,
        description="Interval in seconds for worker to poll for new jobs"
    )
    
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
    
    # Airbyte MongoDB Destination Settings
    airbyte_mongodb_host: str = Field(
        default="mongodb",
        description="MongoDB host for Airbyte destination (container name)"
    )
    airbyte_mongodb_port: int = Field(
        default=27017,
        description="MongoDB port for Airbyte destination"
    )
    airbyte_mongodb_database: str = Field(
        default="rag_db",
        description="MongoDB database for Airbyte to write synced data"
    )
    
    # Web Search Settings (Brave Search API)
    brave_search_api_key: str = Field(
        default="BSALIxHlOobIdrJfmAgRPO1Y7RkkktH",
        description="Brave Search API key for web search functionality"
    )
    
    @property
    def brave_api_key(self) -> str:
        """Alias for brave_search_api_key for compatibility."""
        return self.brave_search_api_key
    
    def get_api_key_for_provider(self, provider: str) -> str:
        """Get the API key for a specific provider.
        
        Args:
            provider: Provider name (openai, google, anthropic, ollama)
        
        Returns:
            API key for the provider, or empty string if not set
        """
        provider = provider.lower()
        if provider == "openai":
            return self.openai_api_key or self.llm_api_key
        elif provider == "google" or provider == "gemini":
            return self.google_api_key or self.llm_api_key
        elif provider == "anthropic" or provider == "claude":
            return self.anthropic_api_key or self.llm_api_key
        elif provider == "ollama":
            return ""  # Ollama doesn't need an API key
        else:
            return self.llm_api_key
    
    def get_orchestrator_api_key(self) -> str:
        """Get the API key for the orchestrator model."""
        return self.get_api_key_for_provider(self.orchestrator_provider)
    
    def get_worker_api_key(self) -> str:
        """Get the API key for the worker model."""
        # First check if a specific fast LLM key is set
        if self.fast_llm_api_key:
            return self.fast_llm_api_key
        return self.get_api_key_for_provider(self.worker_provider)
    
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
