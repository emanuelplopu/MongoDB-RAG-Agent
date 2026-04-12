# RecallHub — Run Blueprint

How to build, start, and access all services.

---

## Quick Start

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

---

## Service Ports

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

> Inside the Docker network, services resolve by hostname: `mongodb:27017`, `backend:8000`, `ollama:11434`.

---

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

---

## Build Commands

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

---

## Environment Configuration

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

> MongoDB connection is pre-configured for Docker (`mongodb://mongodb:27017`). No changes needed for local Docker deployment.

Full list of 140+ options is documented in [.env.example](../.env.example).

---

## Startup Sequence

`start.ps1` follows this order:

1. **Validate** — Docker and Docker Compose are installed and running
2. **Environment** — `.env` file exists (copies from `.env.example` if missing)
3. **Directories** — Creates `data/mongoDB/db` and `data/mongoDB/configdb` if needed
4. **MongoDB** — `docker compose up -d mongodb` → polls health check (up to 60 s)
5. **Backend** — `docker compose up -d backend` → polls `http://localhost:11000/health` (up to 60 s)
6. **Frontend** — `docker compose up -d frontend` → waits 3 s
7. **Status** — Prints container status table and all service URLs

---

## Request Timeouts

| Endpoint Type | Timeout |
|---|---|
| Health checks (`/health`) | 5 seconds |
| Standard API requests | 30 seconds |
| Chat / Agent endpoints (`/api/v1/sessions/`, `/api/v1/chat/`) | 300 seconds (5 min) |

The nginx proxy in the frontend container mirrors these timeouts: 120 s for general API calls, 300 s for chat/session message endpoints. SSE streaming endpoints have buffering disabled.

---

## Useful Commands

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
