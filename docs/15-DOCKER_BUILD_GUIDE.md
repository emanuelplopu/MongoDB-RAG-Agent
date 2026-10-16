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
│  recallhub-backend-base:latest (~13GB)                      │
│  ├── Python 3.11 + venv                                     │
│  ├── PyTorch, Transformers, Whisper, Docling (~11GB)        │
│  ├── FastAPI, Pydantic, LiteLLM, etc. (~500MB)              │
│  ├── Playwright + Chromium browser (~400MB)                 │
│  └── Docling models: layout, TableFormer, RapidOCR (~0.8GB) │
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

### Docling models in the base image

Docling downloads its layout, table-structure and OCR models from
huggingface.co the first time a scanned page is converted. Containers have no
persistent cache, so without countermeasures every recreated backend or
worker container downloads ~0.5 GB again and scanned documents fail entirely
on an offline install. `backend/Dockerfile.base` therefore runs

```
docling-tools models download -o /opt/docling-models layout tableformer rapidocr
```

at build time and sets `DOCLING_ARTIFACTS_PATH=/opt/docling-models`. Docling
reads that variable (`docling.datamodel.settings`) whenever the pipeline
options carry no explicit `artifacts_path`, which is the case for every
converter in `src/ingestion/ingest.py`. The models are part of the base image,
so `-Fast` / `-Worker` builds and the offline image export pick them up with
no extra step, and no cache volume is needed. The layer is ~0.8 GB
(`docling-project--docling-layout-heron` and its ONNX twin, 164 MB each;
`docling-project--docling-models` with TableFormer, 342 MB; `RapidOcr` with
the PP-OCRv6 small detector/recogniser in ONNX and torch form, 62 MB).

Rules that follow from this:

- `.\build-backend.ps1 -Base` needs internet access to huggingface.co (the
  model layer is cached by BuildKit, so a rebuild without dependency changes
  does not download again).
- With an artifacts path set, docling does **not** fall back to a download for
  a missing model. If you enable another docling feature in the ingestion
  pipeline (code/formula enrichment, picture classification, a VLM pipeline,
  EasyOCR), add its model to the `docling-tools models download` line in the
  same change (`docling-tools models download --help` lists the names).
- Check a running container with
  `docker exec rag-ingestion-worker sh -c 'ls $DOCLING_ARTIFACTS_PATH'`.
- Whisper (audio transcription) still downloads its model on first use; it is
  not covered by this layer.

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
│   ├── Dockerfile.base            # Base image (deps + Docling models)
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
