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

#### Whisper checkpoint (audio/video transcription)

Audio files and the audio track of videos are transcribed with openai-whisper
through Docling's ASR pipeline (`src/ingestion/ingest.py`, `MediaConfig`; see
`docs/04-EMBEDDING_AND_QUERY_SYSTEM.md`, "Media transcription"). Docling hands
`whisper.load_model(name, download_root=DOCLING_ARTIFACTS_PATH)` the same
artifacts path, and whisper looks for the checkpoint directly in it under its
own file name: `turbo` is `large-v3-turbo.pt` (1.6 GB). Until 2026-11-07 that
file was not part of the image, so every conversion worker process downloaded
it at its first recording (16 slots stalled for an hour on a throttled uplink
on 2026-11-06). `backend/Dockerfile.base` now fetches it at build time right
after the docling models:

```
ARG WHISPER_MODELS="turbo"
RUN python -c "import os, whisper; ... whisper._download(whisper._MODELS[name], root, False) ..."
```

`whisper._download` verifies the SHA-256 that openai-whisper pins for the
release, so the layer is reproducible. The pipeline records a model that is not
under the artifacts path as an error (`[Error: Whisper model ... is not
available under /opt/docling-models ...]`) instead of downloading. If the media
config selects another size (`media.model`, `INGEST_MEDIA_MODEL`), bake it too:

```powershell
docker build -f backend/Dockerfile.base --build-arg WHISPER_MODELS="turbo small" -t recallhub-backend-base:latest .
```

(`.\build-backend.ps1 -Base` builds with the default, `turbo` only.) Check a
container with `docker exec rag-ingestion-worker sh -c 'ls -la $DOCLING_ARTIFACTS_PATH/*.pt'`.
A GPU for the transcription comes from `docker-compose.media-gpu.yml` (the
device reservation the `embedder-gpu` profile uses, for the ingestion worker,
with the Docling models pinned to the CPU); without it Whisper runs on the CPU
with the media config's thread count.

On a box where a full base rebuild is unwanted (it re-resolves every floating
dependency), the checkpoint can be added as one layer on the existing base:
download `large-v3-turbo.pt` from the URL in `whisper._MODELS["turbo"]`, check
its SHA-256 against the hash in that URL, and build
`FROM recallhub-backend-base:latest` + `COPY large-v3-turbo.pt /opt/docling-models/`
as the new `recallhub-backend-base:latest` (done on the Quellex box on 2026-11-11).

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
