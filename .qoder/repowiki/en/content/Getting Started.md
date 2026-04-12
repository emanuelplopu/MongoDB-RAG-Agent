# Getting Started

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [.env.example](file://.env.example)
- [pyproject.toml](file://pyproject.toml)
- [src/test_config.py](file://src/test_config.py)
- [src/settings.py](file://src/settings.py)
- [src/dependencies.py](file://src/dependencies.py)
- [src/providers.py](file://src/providers.py)
- [src/cli.py](file://src/cli.py)
- [src/ingestion/ingest.py](file://src/ingestion/ingest.py)
- [docker-compose.yml](file://docker-compose.yml)
- [profiles.yaml](file://profiles.yaml)
- [scripts/create_admin.py](file://scripts/create_admin.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [MongoDB Atlas Setup](#mongodb-atlas-setup)
5. [Environment Configuration](#environment-configuration)
6. [Configuration Validation](#configuration-validation)
7. [Run the Ingestion Pipeline](#run-the-ingestion-pipeline)
8. [Create Search Indexes in MongoDB Atlas](#create-search-indexes-in-mongodb-atlas)
9. [Run the Agent](#run-the-agent)
10. [Troubleshooting Guide](#troubleshooting-guide)
11. [Appendices](#appendices)

## Introduction
This guide helps you quickly set up MongoDB-RAG-Agent from scratch. You will install the UV package manager, prepare your environment, connect to MongoDB Atlas, configure API keys, validate your setup, ingest documents, create search indexes, and run the agent.

## Prerequisites
- Python 3.10 or newer
- MongoDB Atlas account (free M0 tier is supported)
- LLM provider API key (for example, OpenRouter or OpenAI)
- Embedding provider API key (recommended OpenAI or OpenRouter)
- UV package manager

**Section sources**
- [README.md](file://README.md#L16-L22)

## Installation
Follow these steps to install and prepare the project.

1. Install UV package manager
   - macOS/Linux:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
   - Windows:
     ```powershell
     powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```

2. Clone the repository and enter the project directory
   ```bash
   git clone https://github.com/coleam00/recallhub.git
   cd recallhub
   ```

3. Create a virtual environment and install dependencies
   ```bash
   uv venv
   source .venv/bin/activate  # Unix/Mac
   .venv\Scripts\activate     # Windows
   uv sync
   ```

4. Confirm Python version meets requirement
   - The project requires Python >= 3.10 as declared in the project configuration.

**Section sources**
- [README.md](file://README.md#L24-L47)
- [pyproject.toml](file://pyproject.toml#L5)

## MongoDB Atlas Setup
Complete the following steps to provision and secure your Atlas cluster.

1. Create a free MongoDB Atlas account and deploy a cluster
   - Choose the free tier (M0) and select a region.
   - Complete the quickstart wizard to configure:
     - Database user (username and password)
     - Network access (allow your current IP address)
   - After deployment, click Connect and choose Drivers to copy your connection string.

2. Update your connection string
   - Paste the copied connection string into your environment configuration as described below.

3. Confirm collections will be created automatically
   - The ingestion process creates the database and collections for you.

**Section sources**
- [README.md](file://README.md#L49-L60)

## Environment Configuration
Configure your environment variables using the example file as a template.

1. Create your .env file from the example
   ```bash
   cp .env.example .env
   ```

2. Edit .env with your credentials
   - MONGODB_URI: Your Atlas connection string
   - LLM_API_KEY: Your LLM provider API key
   - EMBEDDING_API_KEY: Your embedding provider API key

3. Optional advanced settings
   - Provider selection and model choices are configurable in the example file.
   - Search weights and result counts can be tuned.
   - Profiles and indexes names are configurable for multi-project setups.

**Section sources**
- [README.md](file://README.md#L62-L73)
- [.env.example](file://.env.example#L1-L87)

## Configuration Validation
Run the built-in configuration validator to confirm your setup is correct.

```bash
uv run python -m src.test_config
```

Expected outcome:
- The validator prints a success summary indicating all checks passed.

If validation fails:
- Review the printed error and ensure all required variables are set in your .env file.

**Section sources**
- [README.md](file://README.md#L74-L80)
- [src/test_config.py](file://src/test_config.py#L15-L102)
- [src/settings.py](file://src/settings.py#L162-L202)

## Run the Ingestion Pipeline
Add your documents to the documents/ folder and run the ingestion pipeline.

1. Place your documents in the documents/ directory
2. Run ingestion
   ```bash
   uv run python -m src.ingestion.ingest -d ./documents
   ```

What happens:
- Documents are processed, chunked, embedded, and stored in MongoDB collections.

Notes:
- The ingestion pipeline connects to MongoDB using your configured URI and writes to the configured database and collections.

**Section sources**
- [README.md](file://README.md#L82-L94)
- [src/ingestion/ingest.py](file://src/ingestion/ingest.py#L126-L167)

## Create Search Indexes in MongoDB Atlas
After ingestion, create the required indexes in Atlas.

1. In the Atlas UI, navigate to Database → Search and Vector Search → Create Search Index
2. Create a Vector Search index named vector_index with:
   - Database: your configured database
   - Collection: chunks
   - JSON configuration specifying embedding vector dimensions and similarity metric
3. Create an Atlas Search index named text_index with:
   - Database: your configured database
   - Collection: chunks
   - JSON configuration enabling full-text search

Wait for both indexes to reach Active status.

**Section sources**
- [README.md](file://README.md#L95-L141)

## Run the Agent
Start the conversational CLI to ask questions and receive answers powered by your indexed knowledge base.

```bash
uv run python -m src.cli
```

What you can do:
- Ask questions and observe the agent’s tool calls and results.
- Use built-in commands to view system info, list profiles, switch profiles, and clear the screen.

**Section sources**
- [README.md](file://README.md#L143-L149)
- [src/cli.py](file://src/cli.py#L183-L297)

## Troubleshooting Guide
Common issues and resolutions:

- MongoDB connection failures
  - Symptom: Errors related to connection or server selection timeouts during initialization.
  - Actions:
    - Verify MONGODB_URI is correct and includes your Atlas credentials.
    - Ensure network access allows your IP address.
    - Confirm the cluster is healthy and reachable.

- Missing or invalid API keys
  - Symptom: Configuration validation reports missing LLM or embedding keys.
  - Actions:
    - Confirm LLM_API_KEY and EMBEDDING_API_KEY are set in .env.
    - Ensure provider base URLs are correct if using OpenRouter or other providers.

- Index creation timing
  - Symptom: Indexes fail to build or remain in Building state.
  - Actions:
    - Ensure ingestion ran successfully and data exists in the chunks collection before creating indexes.

- Python version mismatch
  - Symptom: Dependency installation or runtime errors.
  - Actions:
    - Confirm your environment uses Python 3.10+ as required by the project configuration.

- Profile-related configuration
  - Symptom: Unexpected database or collection names.
  - Actions:
    - Check ACTIVE_PROFILE and PROFILES_PATH in .env.
    - Review profiles.yaml for correct database and index names.

- Admin user setup (optional)
  - If you need to manage admin users in MongoDB, use the provided script to create or update a superadmin user.

**Section sources**
- [src/dependencies.py](file://src/dependencies.py#L53-L86)
- [src/test_config.py](file://src/test_config.py#L78-L102)
- [src/settings.py](file://src/settings.py#L194-L202)
- [profiles.yaml](file://profiles.yaml#L1-L81)
- [scripts/create_admin.py](file://scripts/create_admin.py#L25-L83)

## Appendices

### Appendix A: Environment Variables Reference
- MONGODB_URI: Atlas connection string
- MONGODB_DATABASE: Target database name
- MONGODB_COLLECTION_DOCUMENTS: Documents collection name
- MONGODB_COLLECTION_CHUNKS: Chunks collection name
- MONGODB_VECTOR_INDEX: Vector search index name
- MONGODB_TEXT_INDEX: Text search index name
- LLM_PROVIDER: LLM provider (openrouter, openai, etc.)
- LLM_API_KEY: LLM provider API key
- LLM_MODEL: LLM model to use
- LLM_BASE_URL: LLM provider base URL
- EMBEDDING_PROVIDER: Embedding provider (openai, etc.)
- EMBEDDING_API_KEY: Embedding provider API key
- EMBEDDING_MODEL: Embedding model to use
- EMBEDDING_BASE_URL: Embedding provider base URL
- EMBEDDING_DIMENSION: Embedding vector dimension
- DEFAULT_MATCH_COUNT: Default number of search results
- MAX_MATCH_COUNT: Maximum allowed search results
- DEFAULT_TEXT_WEIGHT: Default text weight for hybrid search
- APP_ENV: Application environment
- LOG_LEVEL: Logging level
- AIRBYTE_ENABLED: Enable Airbyte integration
- AIRBYTE_API_URL: Airbyte API URL
- AIRBYTE_WEBAPP_URL: Airbyte WebApp URL
- PROFILES_PATH: Path to profiles.yaml
- ACTIVE_PROFILE: Override active profile

**Section sources**
- [.env.example](file://.env.example#L1-L87)

### Appendix B: Docker Compose Overview
The stack includes:
- MongoDB Atlas local container (vector and text search ready)
- Backend service exposing REST API
- Frontend service served via Nginx
- Optional CLI container for terminal access

Ports and services are defined in the compose file. Environment variables for providers and databases are passed to containers.

**Section sources**
- [docker-compose.yml](file://docker-compose.yml#L15-L143)