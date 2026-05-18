# RecallHub Docker Build Guide

## Quick Reference

| Scenario | Command | Time |
|----------|---------|------|
| **Code change (both services)** | `.\build-backend.ps1 -All` | ~5 sec |
| **Backend code only** | `.\build-backend.ps1 -Fast` | ~3 sec |
| **Worker code only** | `.\build-backend.ps1 -Worker` | ~1 sec |
| **First time / Dependency change** | `.\build-backend.ps1 -Base` | ~15 min |
| **Check base image status** | `.\build-backend.ps1 -Check` | instant |

---

## Build Architecture

### Two-Tier System with Shared Base

```
┌─────────────────────────────────────────────────────────────┐
│  recallhub-backend-base:latest (~5GB)                       │
│  ├── Python 3.11 + venv                                     │
│  ├── PyTorch, Transformers, Whisper, Docling (~4GB)         │
│  ├── FastAPI, Pydantic, LiteLLM, etc. (~500MB)              │
│  └── Playwright + Chromium browser (~400MB)                 │
│                                                             │
│  BUILD ONCE - Only rebuild when dependencies change         │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
┌─────────────────────────────┐ ┌─────────────────────────────┐
│  mongodb-rag-agent-backend  │ │  mongodb-rag-agent-         │
│          :latest            │ │  ingestion-worker:latest    │
│  ├── FROM base:latest       │ │  ├── FROM base:latest       │
│  └── COPY src/, backend/    │ │  └── COPY src/, backend/    │
│                             │ │                             │
│  Backend API (~3 sec build) │ │  Worker process (~1s build) │
└─────────────────────────────┘ └─────────────────────────────┘
```

---

## Dockerfiles

| File | Purpose | When to Use |
|------|---------|-------------|
| `backend/Dockerfile.base` | Pre-built base with all deps | One-time setup |
| `backend/Dockerfile.fast` | Backend code-only layer | Daily development |
| `backend/Dockerfile.worker` | Worker code-only layer | Daily development |
| `backend/Dockerfile.ml-heavy` | Full multi-stage build | CI/CD or clean rebuild |

---

## Workflow

### Initial Setup (One-Time)
```powershell
# Build base image with all dependencies (~15 min)
.\build-backend.ps1 -Base
```

### Daily Development
```powershell
# Fast rebuild both services after code changes (~5 sec)
.\build-backend.ps1 -All

# Or rebuild individual services:
.\build-backend.ps1 -Fast     # Backend only
.\build-backend.ps1 -Worker   # Ingestion worker only
```

### When Dependencies Change
Edit `requirements-ml-heavy.txt` or `requirements-api.txt`, then:
```powershell
# Rebuild base image
.\build-backend.ps1 -Base

# Then fast build both services
.\build-backend.ps1 -All
```

### Check Status
```powershell
# See if base image exists
.\build-backend.ps1 -Check

# List all images
docker images | Select-String "recallhub|mongodb-rag"
```

---

## Troubleshooting

### Base image not found
```powershell
.\build-backend.ps1 -Base  # Build it first
```

### Container not picking up code changes
```powershell
.\build-backend.ps1 -All  # Rebuild and restart both services
```

### Need complete clean rebuild
```powershell
docker compose build --no-cache backend ingestion-worker  # Full rebuild (~15 min)
```

### Check what's in the container
```powershell
docker exec rag-backend wc -l /app/backend/routers/system.py
docker exec rag-ingestion-worker wc -l /app/backend/workers/ingestion_worker.py
```

---

## Files Reference

```
MongoDB-RAG-Agent/
├── build-backend.ps1              # Build helper script
├── backend/
│   ├── Dockerfile.base            # Base image (deps only)
│   ├── Dockerfile.fast            # Fast backend build (code only)
│   ├── Dockerfile.worker          # Fast worker build (code only)
│   ├── Dockerfile.ml-heavy        # Full multi-stage (CI/CD)
│   ├── requirements-ml-heavy.txt  # Heavy ML deps (torch, etc.)
│   └── requirements-api.txt       # API deps (fastapi, etc.)
└── docker-compose.yml             # Uses fast Dockerfiles
```

---

## IMPORTANT: Default Behavior

- `docker compose build backend` uses `Dockerfile.fast` (fast, code only)
- `docker compose build ingestion-worker` uses `Dockerfile.worker` (fast, code only)
- `.\build-backend.ps1 -All` builds both and restarts containers (~5 sec)

**Always use `.\build-backend.ps1 -All` for code changes!**
