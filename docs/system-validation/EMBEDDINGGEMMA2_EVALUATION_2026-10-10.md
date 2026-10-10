# EmbeddingGemma 2 — feasibility, performance and migration estimate for RecallHub

Date: 2026-10-10 · Host: Ryzen AI 9 HX 470 (12 vCPU to Docker Desktop, 36.8 GB VM RAM), no NVIDIA GPU · Repo state audited: `staging-strategy-os` @ `ae579b5`

## TL;DR

| Question | Answer |
|---|---|
| Can we use it? | **Yes, but not through Ollama today.** Every Ollama tag (`270m`, `latest`, `*-bf16`, …) fails on Windows/Linux with `this model requires MLX support` (MLX = Apple-Silicon runtime; maintainer: "not yet available on Linux and Windows", [ollama/ollama#18825](https://github.com/ollama/ollama/issues/18825)). It runs fine via **llama.cpp server** (`ghcr.io/ggml-org/llama.cpp:server` + `ggml-org/embeddinggemma-2-GGUF`), which exposes the same OpenAI-compatible `/v1/embeddings` the repo already talks to. |
| Text quality | Published MTEB multilingual v2 **61.36** (768-d), English v2 68.46, code 78.68. Local sanity on 400 German legal/business chunks: pseudo-query recall top-1 13/20, **top-5 20/20**. Top-10 neighbour overlap with the stored `text-embedding-3-large` vectors is 60% — the two models rank differently; a labelled eval is needed before cut-over. |
| Images (schematics) | **Works** through llama.cpp (`/embeddings` with `{"prompt_string","multimodal_data"}`); text queries rank the matching screenshot top-1 in all 3/3 tests. 3–9 s per image on CPU. |
| Throughput (CPU, Docker, Q8_0) | ~**1,100 Gemma tokens/s**, ~4–20 chunks/s depending on chunk length. Warm query latency **68 ms p50 / 84 ms p95**. Concurrency does not help (CPU-bound). |
| Re-embedding the current index | 435,492 chunks ≈ **76 M Gemma tokens** → **~19–20 h on this laptop's Docker CPU**; est. 30–60 min on a single modern NVIDIA GPU (`server-cuda` image). OpenAI equivalent for comparison: ~$8.6 with 3-large, ~$1.3 with 3-small. |
| Storage win | 3072-d → 768-d cuts the `chunks` collection from **18.1 GB to ≈4.6 GB** (BSON double arrays), or ≈1.5 GB with BSON binary float32 vectors; vector-index RAM from ≈5.3 GB to ≈1.3 GB. |
| Engineering effort | ~1 week: embedder service in compose, a *shared* embedding client with task prefixes (today there are 5 independent OpenAI-SDK call sites), re-embed job with blue/green index switch, eval. +2–3 days for image-chunk ingestion. |

## 1. The model

Sources: [Ollama library page](https://ollama.com/library/embeddinggemma-2), [Google model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2), [Google launch post](https://blog.google/innovation-and-ai/technology/developers-tools/embeddinggemma-2/), [ggml-org GGUF](https://huggingface.co/ggml-org/embeddinggemma-2-GGUF), [unsloth GGUF](https://unsloth.ai/docs/models/embeddinggemma-2).

- Released 2026-10-06 by Google DeepMind, **Apache 2.0**, built on the Gemma 4 architecture.
- 740 M params total = 270 M text backbone (130 M transformer + 140 M embedder) + 170 M vision encoder + 300 M audio encoder; encoders load selectively → 270m / 440m / 570m / 740m variants, all mapping into **one shared 768-d space**.
- Matryoshka (MRL) truncation to 512 / 256 / 128 (re-normalise after truncation). Model card: "close to lossless down to 256".
- Context **8,192 tokens** (Ollama tag listing says 256K — that is the architecture's window, not what the model was trained for; the model card says 8K). Images cost ~280 tokens each (we measured 468 incl. marker for a 1,600-px screenshot).
- **Task prefixes are required for best quality** (model card: omitting them "reduces precision"):
  - query: `task: search result | query: {q}`
  - document: `title: {title} | text: {content}` or `title: none | text: {content}`
  - images/audio: no prefix.
- Precision: use bf16/fp32 or integer quants; **fp16 can produce NaNs** (model card warning). Q8_0 GGUF is reported at mean cosine 0.99993 vs BF16 ([ngquocvinh/EmbeddingGemma-2-GGUF](https://huggingface.co/ngquocvinh/EmbeddingGemma-2-GGUF)).

Published benchmarks (768-d, company-reported; no independent MTEB runs found yet):

| Dim | MTEB multilingual v2 | MTEB eng v2 | MTEB code v1 | MMEB v2 VisDoc |
|---|---|---|---|---|
| 768 | 61.36 | 68.46 | 78.68 | 67.84 |
| 512 | 61.17 | 68.41 | 77.24 | — |
| 256 | 60.41 | 67.78 | 76.18 | — |
| 128 | 57.89 | 65.68 | 71.41 | — |

For reference, OpenAI quotes `text-embedding-3-large` at 64.6 MTEB (v1 English) / 54.9 MIRACL and `text-embedding-3-small` at 62.3 / 44.0 — different benchmark versions, so not directly comparable.

## 2. Runtime validation on this machine

### Ollama — blocked (as of 2026-10-10)

```
ollama pull embeddinggemma-2:270m          → Error: this model requires MLX support, but the MLX runtime is not available
ollama pull embeddinggemma-2:latest        → same
ollama pull embeddinggemma-2:270m-bf16-text → same
ollama pull embeddinggemma-2:740m-bf16     → same
```

Ollama 0.40.2 on Windows. The [v0.40.0 release notes](https://github.com/ollama/ollama/releases) introduced the model as "MLX now has support for an embedding model: embeddinggemma-2"; MLX is their Apple-Silicon runtime. Expect a GGUF-backed tag eventually; until then the repo's existing Ollama wiring (`docker-compose.local.yml`, `installer/`) cannot serve this model.

### llama.cpp server in Docker — works

```powershell
docker run -d --name eg2-bench -p 18080:8080 -v <cache>:/root/.cache/llama.cpp `
  ghcr.io/ggml-org/llama.cpp:server -hf ggml-org/embeddinggemma-2-GGUF:Q8_0 `
  --embeddings --host 0.0.0.0 --port 8080 -c 32768 -b 8192 -ub 8192 -np 4 -t 12
```

Image built 2026-10-10 (llama.cpp `b11541`). It auto-downloaded `embeddinggemma-2-Q8_0.gguf` (310 MB) **and** `mmproj-embeddinggemma-2-Q8_0.gguf`; `/props` reports `modalities: {vision: true, video: true, audio: true}`. Container RSS 4.2 GB with these (generous) context/batch settings. GPU variants of the same image exist: `server-cuda`, `server-vulkan`, `server-rocm`.

API behaviour verified:

| Call | Result |
|---|---|
| `POST /v1/embeddings {"input": [str, …]}` | OK, 768-d, L2-normalised, `usage.prompt_tokens` returned — drop-in for the repo's OpenAI SDK calls |
| `dimensions=256` parameter | **ignored** (returns 768) → truncate + re-normalise client-side |
| `POST /embeddings {"content": {"prompt_string": "<media_marker>", "multimodal_data": ["<b64 png>"]}}` | OK, image embedded (468 tokens, vector differs from marker-only, cos 0.62) |
| `{"content": "<marker>", "multimodal_data": […]}` (flat form) | **silently ignores the image** (32 tokens, identical vector for every image) — easy trap |
| OpenAI `image_url` content parts | 400 |

### Measured performance (CPU, Q8_0, 12 threads, other containers running)

| Workload | Result |
|---|---|
| Query, cold (model already resident) | 75 ms |
| Query, warm | **68 ms p50, 84 ms p95** |
| Documents, batch 1 (mean 134 cl100k tok) | 12 chunks/s |
| Documents, batch 64 (mean 134 cl100k tok) | 20 chunks/s ≈ 870 Gemma tok/s |
| Documents, 512 longer chunks (mean 263 Gemma tok), batch 64 | 4.1 chunks/s ≈ **1,076 Gemma tok/s** |
| Same, 4 concurrent clients × batch 16 / 8 × 8 / 4 × 64 | 4.3 chunks/s ≈ 1,127–1,141 tok/s (no scaling — CPU-bound) |
| Image embed (60–1,400 KB PNG) | 2.9–8.6 s each |

Gemma tokenizer yields **1.133×** the cl100k token count on this German corpus (measured on 512 chunks).

### Quality sanity checks on real `rag_parhelion` chunks (n = 400)

| Check | Result |
|---|---|
| Pseudo-query (first sentence of chunk, with query prefix) → source chunk | top-1 13/20, **top-5 20/20** |
| Top-10 neighbour overlap: OpenAI 3-large (3072) vs EG2 (768) | 0.60 |
| Top-10 overlap: EG2 768 vs 512 (MRL) | **0.915** |
| Top-10 overlap: EG2 768 vs 256 (MRL) | 0.83 |
| Text query → 3 screenshots, correct image ranked first | 3/3 (cos 0.57 / 0.64 / 0.74) |

Interpretation: 512-d is the safe MRL compromise on this data; 256-d visibly changes rankings. The 0.60 overlap with OpenAI is not a quality verdict — it says the models disagree enough that cut-over must be gated on a labelled retrieval eval (the repo's `backend/evaluation/` can host it). Cross-modal cosine values sit in a lower band (0.45–0.75) than text-text, and one image attracted the highest score for *unrelated* queries (hubness) — image chunks need their own score calibration or a modality filter rather than naive mixing.

## 3. Current index data (measured 2026-10-10 on `rag-mongodb`)

| DB / collection | Chunks (with embedding) | Dim | Mean tokens (cl100k) | p95 / max | Est. total tokens | Data size | On disk |
|---|---|---|---|---|---|---|---|
| `rag_parhelion.chunks` | **432,417** | 3072 | 153 | 378 / 876 | 66.2 M | 18,112 MB | 13,256 MB |
| `rag_test_law.chunks` | 3,075 | 3072 | 195 | 477 / 615 | 0.6 M | 127 MB | 130 MB |
| others (`rag_db`, `recallhub`, `user_rag_*`) | 0 | — | — | — | — | — | — |

Both live collections have `vector_index` (`numDimensions: 3072`, cosine, no filter fields) and `text_index` (lucene.standard on `content`). Average object size is 42.9 KB — the 3072-d embedding stored as a BSON array of doubles is ≈43 KB on its own (~14 B per element incl. array keys), i.e. **~97% of every chunk document is the vector**.

Chunk text field: `content`; metadata records only `metadata.embedding_model`, not provider or dimension.

## 4. What re-embedding would take

Volume: 66.8 M cl100k tokens × 1.133 ≈ **76 M Gemma tokens** (document prefix included), 435,492 chunks.

| Where | Measured / estimated rate | Wall time |
|---|---|---|
| This laptop, Docker CPU, Q8_0 (measured) | ~1,100 tok/s | **~19–20 h** |
| Host llama.cpp + Vulkan on Radeon 890M (not measured) | est. 3–5× CPU | ~4–7 h |
| One modern NVIDIA GPU, `server-cuda` image (est.) | 20–50 K tok/s | **~30–60 min** |
| OpenAI API, for comparison | — | `text-embedding-3-large` ≈ $8.6 (≈$4.3 Batch API), `3-small` ≈ $1.3 ([pricing](https://developers.openai.com/api/docs/models/text-embedding-3-small)) |

Mongo side: a bulk `updateOne` per chunk with the new vector is minutes, not hours; recreating the 768-d vector index on 432 K docs is minutes to low tens of minutes on the local Atlas image.

Storage after migration (`rag_parhelion.chunks`):

| Vector encoding | Bytes / chunk vector | Collection data (est.) | Vector index RAM (float32 HNSW) |
|---|---|---|---|
| today: 3072-d BSON double array | ~43 KB | 18.1 GB | ~5.3 GB |
| 768-d BSON double array | ~10.7 KB | **~4.6 GB** | ~1.3 GB |
| 768-d BSON binary float32 (`bsonBinaryVector`) | 3 KB | ~1.5 GB | ~1.3 GB |
| 512-d binary float32 | 2 KB | ~1.1 GB | ~0.9 GB |

Query cost per distance also drops 4× (768 vs 3072 floats), which is the main lever for `$vectorSearch` latency at the current `numCandidates`.

## 5. Fit with the codebase (from the audit of `staging-strategy-os`)

What already helps:

- Config surface exists: `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL`, `EMBEDDING_DIMENSION` in `.env.example`, `src/settings.py:76-94`, `backend/core/config.py:54-58`, and all four compose services. `.env` even carries a commented `EMBEDDING_DIMENSION=768`.
- Every query-side call site uses the OpenAI SDK with `settings.embedding_base_url` (`backend/routers/search.py:17-31`, `backend/routers/chat.py:25-39`, `backend/agent/federated_search.py:55-69`, `src/dependencies.py:75-115`), and the ingestion `openai` branch in `src/ingestion/embedder.py:84-91,296-299` does the same → pointing `EMBEDDING_BASE_URL` at the llama.cpp server with `EMBEDDING_PROVIDER=openai` works for text **without code changes** (verified request/response shape).
- Index creation already derives the dimension from settings (`src/setup_indexes.py:34-65`) or from the first stored chunk (`scripts/full-restore.ps1:628-630`).

What must change (blockers for a *correct* migration, not for a demo):

1. **No task-prefix concept anywhere.** Documents need `title: none | text: `, queries `task: search result | query: `. With five independent call sites, add one shared embedding client (e.g. `backend/services/embedding_client.py`, reused by `src/ingestion/embedder.py`) that owns prefixes, truncation + re-normalisation, and dimension validation.
2. **Silent cross-provider fallback** (`src/ingestion/embedder.py:684-715`): if the local server is down, ingestion falls back to OpenAI and stores 3072-d vectors with only a warning (`:709-714`). Must become a hard error during/after migration.
3. **Ingestion ignores runtime overrides** (`load_settings(use_profile=False)` at `embedder.py:86,180`), while UI/profile/model-version switches only change the query side (`backend/routers/system.py:1252-1406`). Query and ingestion can drift onto different models — record `provider`, `model`, `dim` on every chunk and validate at query time.
4. The `ollama` provider branch is hard-wired to `nomic-embed-text @ localhost:11434` (`embedder.py:56-57,367-376`) — don't use it; use the `openai` branch against llama.cpp (or fix it when Ollama ships a non-MLX build).
5. `dimensions` isn't honoured server-side; the OpenAI call at `embedder.py:296-299` doesn't send it either. Truncation must be explicit client-side.
6. Stale 1536 literals: `scripts/init-mongodb.js:34`, `src/ingestion/ingest.py:2509`, `README.md:138`; `backend/routers/system.py:31-39` model table is OpenAI-only (the UI dropdown reads it).
7. Backup/restore via API (`backend/services/backup_service.py:273-276`) drops the collection and never recreates search indexes — pre-existing, but it bites harder when dimensions change.

## 6. Recommended plan

Phase 0 — decide dimension: **768** for maximum quality or **512** (91.5% ranking agreement, −33% storage). Not 256 on this corpus.

Phase 1 — infrastructure (½ day)
- Add an `embedder` service to `docker-compose.yml`: `ghcr.io/ggml-org/llama.cpp:server` (or `server-cuda` on GPU hosts), `-hf ggml-org/embeddinggemma-2-GGUF:Q8_0 --embeddings -c 8192 -np 4`, healthcheck on `/health`, internal port 8080. Keep the GGUF in a named volume so offline installs work (`scripts/export-docker-images.ps1` already handles images; add the model file).
- `EMBEDDING_PROVIDER=openai`, `EMBEDDING_BASE_URL=http://embedder:8080/v1`, `EMBEDDING_MODEL=embeddinggemma-2`, `EMBEDDING_DIMENSION=768`, dummy `EMBEDDING_API_KEY`.

Phase 2 — shared embedding client (1–2 days incl. tests)
- Prefix handling, truncation/re-normalisation, batch of 64, hard-fail on dimension mismatch, no cross-provider fallback; store `embedding_model` / `embedding_provider` / `embedding_dim` on chunks; make `search.py`, `chat.py`, `federated_search.py`, `src/tools.py` and `src/ingestion/embedder.py` use it.

Phase 3 — blue/green re-embed (1 day code + compute)
- Worker job that streams `chunks` in batches of 64, writes `embedding_v2` (binary float32 if the Atlas Local version supports `bsonBinaryVector`), creates `vector_index_v2` on it, then flips config and drops the old field/index. Old index stays queryable throughout. Compute: ~20 h on this laptop CPU, <1 h on a GPU host.

Phase 4 — eval gate (1 day)
- Labelled German query set through `backend/evaluation/` against both indexes before cut-over; the 0.60 ranking overlap means this is not optional.

Phase 5 (optional, the schematics case) — image chunks (2–3 days)
- Ingestion: for pages where Docling yields little text but has `PictureItem`s, rasterise and embed the image via `/embeddings` with `multimodal_data`; store as chunks with `modality: "image"` and a thumbnail/page reference. Retrieval: either a modality pre-filter + separate score normalisation, or a VLM-description fallback (a local `gemma4` via Ollama is already available) — evaluate both; cross-modal scores live in a different band than text-text.

## 7. Open points / caveats

- All EG2 benchmark numbers are Google's; no independent MTEB runs existed on 2026-10-10.
- Throughput was measured on a laptop with ~40 other containers running; treat it as a floor.
- Image embedding on CPU is 3–9 s per image — fine for ingestion in the worker, not for query-time image search without a GPU.
- Ollama's Windows/Linux build is expected to arrive; when it does, the compose `ollama` service becomes an alternative, but the task-prefix and fallback fixes above are needed regardless of runtime.
- The benchmark container `eg2-bench` (port 18080) was left running; stop with `docker rm -f eg2-bench`. The GGUF is cached in the session scratchpad and will be deleted with it.
