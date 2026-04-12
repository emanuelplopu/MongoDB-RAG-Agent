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
- [docs/RUN_BLUEPRINT.md](file://docs/RUN_BLUEPRINT.md)
- [start.ps1](file://start.ps1)
- [build.ps1](file://build.ps1)
- [build-backend.ps1](file://build-backend.ps1)
- [installer/scripts/Start-Services.ps1](file://installer/scripts/Start-Services.ps1)
</cite>

## Update Summary
**Changes Made**
- Added comprehensive PowerShell automation infrastructure documentation
- Integrated RUN_BLUEPRINT.md as the primary operational guide
- Documented Docker Compose configuration variations (production, development, local, Airbyte)
- Added detailed service port configurations and startup sequences
- Included advanced build system with two-stage Docker architecture
- Documented WSL2 integration for Windows users

## Table of Contents
1. [Introduction](#introduction)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [PowerShell Automation Infrastructure](#powershell-automation-infrastructure)
5. [Docker Compose Configurations](#docker-compose-configurations)
6. [Service Architecture and Ports](#service-architecture-and-ports)
7. [Environment Configuration](#environment-configuration)
8. [Configuration Validation](#configuration-validation)
9. [Run the Ingestion Pipeline](#run-the-ingestion-pipeline)
10. [Create Search Indexes in MongoDB Atlas](#create-search-indexes-in-mongodb-atlas)
11. [Run the Agent](#run-the-agent)
12. [Advanced Build System](#advanced-build-system)
13. [Troubleshooting Guide](#troubleshooting-guide)
14. [Appendices](#appendices)

## Introduction
This guide helps you quickly set up MongoDB-RAG-Agent from scratch using the comprehensive PowerShell automation infrastructure. The project now provides a complete operational blueprint with automated service management, advanced build systems, and multiple deployment scenarios including Docker Compose, local development, offline operation with Ollama, and Airbyte integration.

## Prerequisites
- Python 3.10 or newer
- MongoDB Atlas account (free M0 tier is supported)
- LLM provider API key (for example, OpenRouter or OpenAI)
- Embedding provider API key (recommended OpenAI or OpenRouter)
- UV package manager
- Docker Desktop (for containerized deployment)
- PowerShell 5.1 or newer (for automation scripts)

**Section sources**
- [README.md:16-22](file://README.md#L16-L22)
- [docs/RUN_BLUEPRINT.md:1-181](file://docs/RUN_BLUEPRINT.md#L1-L181)

## Installation
Follow these steps to install and prepare the project using the PowerShell automation infrastructure.

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
- [README.md:24-47](file://README.md#L24-L47)
- [pyproject.toml](file://pyproject.toml#L5)

## PowerShell Automation Infrastructure
The project provides comprehensive PowerShell automation for streamlined development and deployment.

### Quick Start with Single Command
```powershell
.\start.ps1
```

This single command handles everything: validates prerequisites, creates data directories, starts MongoDB → Backend → Frontend in order with health checks, and prints all URLs when ready.

### start.ps1 Flags
| Flag | Description |
|---|---|
| _(none)_ | Start all services |
| `-Build` | Rebuild containers, then start |
| `-Down` | Stop all services |
| `-Logs` | Start and show live log output |
| `-Clean` | Remove volumes + data, rebuild from scratch |

**Section sources**
- [docs/RUN_BLUEPRINT.md:7-24](file://docs/RUN_BLUEPRINT.md#L7-L24)
- [start.ps1:16-21](file://start.ps1#L16-L21)

## Docker Compose Configurations
The project uses composable YAML files layered on top of the base `docker-compose.yml`.

### Production (default)
```powershell
.\start.ps1
# or manually:
docker compose up -d
```

### Development — hot reload
```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

- Mounts `backend/` and `src/` as volumes
- Runs uvicorn with `--reload` — code changes take effect instantly, no rebuild needed
- Use this for daily development

### Local / Offline — with Ollama
```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml up
```

- Adds an Ollama container on port `11434` with GPU passthrough
- Configures LLM and embedding providers to use Ollama instead of cloud APIs
- Fully offline operation (no API keys needed)

### With Airbyte (Confluence / Jira integration)
```powershell
docker compose -f docker-compose.yml -f docker-compose.airbyte.yml up -d
```

- Adds Airbyte services on ports `11020`–`11022`

**Section sources**
- [docs/RUN_BLUEPRINT.md:47-86](file://docs/RUN_BLUEPRINT.md#L47-L86)
- [docker-compose.yml:1-193](file://docker-compose.yml#L1-L193)

## Service Architecture and Ports
All services are exposed on `localhost` in the **11000–11100** port range.

| Service | Host Port | Container Port | URL |
|---|---|---|---|
| **Frontend** (React + nginx) | `11080` | 80 | http://localhost:11080 |
| **Backend API** (FastAPI) | `11000` | 8000 | http://localhost:11000 |
| **Swagger UI** | `11000` | 8000 | http://localhost:11000/docs |
| **ReDoc** | `11000` | 8000 | http://localhost:11000/redoc |
| **MongoDB** (Atlas Local) | `11017` | 27017 | `mongodb://localhost:11017` |
| **Ollama** _(optional)_ | `11434` | 11434 | http://localhost:11434 |
| **Airbyte Webapp** _(optional)_ | `11020` | — | http://localhost:11020 |
| **Airbyte API** _(optional)_ | `11021` | — | — |
| **Airbyte Builder** _(optional)_ | `11022` | — | — |

Inside the Docker network, services resolve by hostname: `mongodb:27017`, `backend:8000`, `ollama:11434`.

**Section sources**
- [docs/RUN_BLUEPRINT.md:27-44](file://docs/RUN_BLUEPRINT.md#L27-L44)
- [docker-compose.yml:4-13](file://docker-compose.yml#L4-L13)

## Environment Configuration
Configure your environment variables using the example file as a template.

1. Copy the template: `cp .env.example .env` (or use `.env.docker` as a starting point)
2. Fill in required values:

| Variable | Required | Description |
|---|---|---|
| `LLM_API_KEY` | Yes | API key for your LLM provider |
| `EMBEDDING_API_KEY` | Yes | API key for embeddings (OpenAI, etc.) |
| `JWT_SECRET_KEY` | Production | Change from default for production use |
| `LLM_PROVIDER` | No | `openai` (default), `openrouter`, `ollama`, `gemini`, `anthropic` |
| `EMBEDDING_PROVIDER` | No | `openai` (default), `google`, `voyageai`, `ollama` |
| `REGISTRATION_MODE` | No | `open` (default), `invite`, `closed` |

MongoDB connection is pre-configured for Docker (`mongodb://mongodb:27017`). No changes needed for local Docker deployment.

Full list of 140+ options is documented in [.env.example](../.env.example).

**Section sources**
- [docs/RUN_BLUEPRINT.md:113-129](file://docs/RUN_BLUEPRINT.md#L113-L129)
- [.env.example:1-141](file://.env.example#L1-L141)

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
- [README.md:74-80](file://README.md#L74-L80)
- [src/test_config.py:15-102](file://src/test_config.py#L15-L102)
- [src/settings.py:162-202](file://src/settings.py#L162-L202)

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
- [README.md:82-94](file://README.md#L82-L94)
- [src/ingestion/ingest.py:126-167](file://src/ingestion/ingest.py#L126-L167)

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
- [README.md:95-141](file://README.md#L95-L141)

## Run the Agent
Start the conversational CLI to ask questions and receive answers powered by your indexed knowledge base.

```bash
uv run python -m src.cli
```

What you can do:
- Ask questions and observe the agent's tool calls and results.
- Use built-in commands to view system info, list profiles, switch profiles, and clear the screen.

**Section sources**
- [README.md:143-149](file://README.md#L143-L149)
- [src/cli.py:183-297](file://src/cli.py#L183-L297)

## Advanced Build System
The project uses a sophisticated two-stage Docker build architecture for optimal development experience.

### Full build (all images)
```powershell
.\build.ps1              # Lightweight (default)
.\build.ps1 heavy        # Full ML stack (transformers, whisper, playwright)
.\build.ps1 frontend     # Frontend only
.\build.ps1 backend      # Backend only (lightweight)
```

### Fast backend rebuild (code-only changes)
```powershell
.\build-backend.ps1 -All     # Rebuild backend + worker (~5 seconds)
.\build-backend.ps1 -Fast    # Backend only
.\build-backend.ps1 -Worker  # Ingestion worker only
.\build-backend.ps1 -Base    # Rebuild base image (after dependency changes, ~15 min)
```

The build uses a **two-stage architecture**: a shared base image (`recallhub-backend-base`) caches all Python dependencies, so code-only rebuilds via `Dockerfile.fast` / `Dockerfile.worker` complete in seconds.

**Section sources**
- [docs/RUN_BLUEPRINT.md:89-109](file://docs/RUN_BLUEPRINT.md#L89-L109)
- [build.ps1:1-149](file://build.ps1#L1-L149)
- [build-backend.ps1:1-255](file://build-backend.ps1#L1-L255)

## Troubleshooting Guide
Common issues and resolutions:

### Docker and Service Management
- **Docker not found**: Ensure Docker Desktop is installed and running
- **Docker Compose not available**: Install Docker Compose plugin
- **Service startup failures**: Check health checks and logs using `docker compose logs -f`
- **Port conflicts**: Services use ports 11000-11100, ensure they're available

### MongoDB Connection Failures
- Symptom: Errors related to connection or server selection timeouts during initialization.
- Actions:
  - Verify MONGODB_URI is correct and includes your Atlas credentials.
  - Ensure network access allows your IP address.
  - Confirm the cluster is healthy and reachable.

### Missing or Invalid API Keys
- Symptom: Configuration validation reports missing LLM or embedding keys.
- Actions:
  - Confirm LLM_API_KEY and EMBEDDING_API_KEY are set in .env.
  - Ensure provider base URLs are correct if using OpenRouter or other providers.

### Index Creation Timing
- Symptom: Indexes fail to build or remain in Building state.
- Actions:
  - Ensure ingestion ran successfully and data exists in the chunks collection before creating indexes.

### Python Version Mismatch
- Symptom: Dependency installation or runtime errors.
- Actions:
  - Confirm your environment uses Python 3.10+ as required by the project configuration.

### Profile-Related Configuration
- Symptom: Unexpected database or collection names.
- Actions:
  - Check ACTIVE_PROFILE and PROFILES_PATH in .env.
  - Review profiles.yaml for correct database and index names.

### Admin User Setup (optional)
- If you need to manage admin users in MongoDB, use the provided script to create or update a superadmin user.

### Windows WSL2 Integration
- **WSL distribution not found**: Run the installer first to set up the WSL environment
- **Docker daemon not starting**: Ensure Docker Desktop is running and WSL2 integration is enabled
- **Service health checks failing**: Verify all required services are running in the WSL2 environment

**Section sources**
- [src/dependencies.py:53-86](file://src/dependencies.py#L53-L86)
- [src/test_config.py:78-102](file://src/test_config.py#L78-L102)
- [src/settings.py:194-202](file://src/settings.py#L194-L202)
- [profiles.yaml:1-81](file://profiles.yaml#L1-L81)
- [scripts/create_admin.py:25-83](file://scripts/create_admin.py#L25-L83)
- [installer/scripts/Start-Services.ps1:211-260](file://installer/scripts/Start-Services.ps1#L211-L260)

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
- [.env.example:1-141](file://.env.example#L1-L141)

### Appendix B: Docker Compose Overview
The stack includes:
- MongoDB Atlas local container (vector and text search ready)
- Backend service exposing REST API
- Frontend service served via Nginx
- Optional CLI container for terminal access

Ports and services are defined in the compose file. Environment variables for providers and databases are passed to containers.

**Section sources**
- [docker-compose.yml:15-193](file://docker-compose.yml#L15-L193)

### Appendix C: PowerShell Automation Commands
Useful commands for managing the system:

```powershell
# View live logs
docker compose logs -f

# View logs for a specific service
docker compose logs -f backend

# Restart a single service (no rebuild)
docker compose restart backend

# Recreate backend after volume mount changes (~2 seconds, no rebuild)
docker compose up -d backend

# Check service health
docker inspect --format='{{.State.Health.Status}}' rag-mongodb
Invoke-WebRequest http://localhost:11000/health

# Open a shell in the backend container
docker compose exec backend bash
```

**Section sources**
- [docs/RUN_BLUEPRINT.md:159-180](file://docs/RUN_BLUEPRINT.md#L159-L180)