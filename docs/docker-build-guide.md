# RecallHub Docker Build Guide

## Quick Reference

| Scenario | Command | Time |
|----------|---------|------|
| **Code change only** | `.\build-backend.ps1 -Fast` | ~10-30 sec |
| **First time / Dependency change** | `.\build-backend.ps1 -Base` | ~15 min |
| **Check base image status** | `.\build-backend.ps1 -Check` | instant |
| **Full rebuild (avoid!)** | `docker compose build backend` | ~15 min |

---

## Build Architecture

### Two-Tier System

```
┌─────────────────────────────────────────────────────────────┐
│  recallhub-backend-base:latest (~10GB)                      │
│  ├── Python 3.11 + venv                                     │
│  ├── PyTorch, Transformers, Whisper, Docling (~8GB)         │
│  ├── FastAPI, Pydantic, LiteLLM, etc. (~500MB)              │
│  └── Playwright + Chromium browser (~400MB)                 │
│                                                             │
│  BUILD ONCE - Only rebuild when dependencies change         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  mongodb-rag-agent-backend:latest (~10GB + code)            │
│  ├── FROM recallhub-backend-base:latest                     │
│  └── COPY src/, backend/, profiles.yaml                     │
│                                                             │
│  BUILD FREQUENTLY - Only copies code (~10-30 seconds)       │
└─────────────────────────────────────────────────────────────┘
```

---

## Dockerfiles

| File | Purpose | When to Use |
|------|---------|-------------|
| `backend/Dockerfile.base` | Pre-built base with all deps | One-time setup |
| `backend/Dockerfile.fast` | Code-only layer on base | Daily development |
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
# Fast rebuild after code changes (~10-30 sec)
.\build-backend.ps1 -Fast
```

### When Dependencies Change
Edit `requirements-ml-heavy.txt` or `requirements-api.txt`, then:
```powershell
# Rebuild base image
.\build-backend.ps1 -Base

# Then fast build
.\build-backend.ps1 -Fast
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
.\build-backend.ps1 -Fast  # Rebuild and restart
```

### Need complete clean rebuild
```powershell
docker compose build --no-cache backend  # Full rebuild (~15 min)
```

### Check what's in the container
```powershell
docker exec rag-backend wc -l /app/backend/routers/system.py
```

---

## Files Reference

```
MongoDB-RAG-Agent/
├── build-backend.ps1           # Build helper script
├── backend/
│   ├── Dockerfile.base         # Base image (deps only)
│   ├── Dockerfile.fast         # Fast build (code only)
│   ├── Dockerfile.ml-heavy     # Full multi-stage
│   ├── requirements-ml-heavy.txt  # Heavy ML deps (torch, etc.)
│   └── requirements-api.txt       # API deps (fastapi, etc.)
└── docker-compose.yml          # Uses Dockerfile.ml-heavy by default
```

---

## IMPORTANT: Default Behavior

- `docker compose build backend` uses `Dockerfile.ml-heavy` (slow, full rebuild)
- `.\build-backend.ps1 -Fast` uses `Dockerfile.fast` (fast, code only)

**Always use `.\build-backend.ps1 -Fast` for code changes!**
