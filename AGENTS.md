# RecallHub Development Instructions

Last reviewed: 2026-10-10

`AGENTS.md` and `CLAUDE.md` must stay functionally identical. Update both in the same change.

## What This Repo Is Now

RecallHub is no longer just the original `examples/` RAG prototype. The active product is:

- `backend/`: production FastAPI API, orchestration, providers, services, auth, backups, cloud sync, search, and admin endpoints.
- `frontend/`: production React 19 + TypeScript + Vite 8 (Tailwind CSS 4, Vitest 5) UI with tenant-aware branding and feature gating.
- `backend/workers/ingestion_worker.py`: background ingestion worker that keeps the API responsive.
- `src/`: still important shared Python code for ingestion, profiles, CLI tooling, and settings integration used by the worker and some backend flows.
- `docker-compose.yml`: the main multi-tenant dev/runtime stack.
- `backend/providers/` and `backend/routers/cloud_sources/`: Google Drive, Dropbox, WebDAV, email, Airbyte-backed sources, OAuth, sync, and cache flows.
- `backend/agent/`: orchestrator-worker agent system with the legacy strategies registry and federated search.
- `backend/agent/strategy/`: **Strategy OS** — config-driven DAG runtime, business context resolver, exploration modes, spec/state stores. The active strategy subsystem.
- `backend/scheduler/`, `backend/evaluation/`, `backend/cli/`: Strategy OS overnight exploration scheduler, evaluation/judging system, and `quellexctl` CLI.

Treat these as supporting or reference material unless the task explicitly targets them:

- `examples/`
- `.claude/reference/`
- older screenshots, generated assets, and exploratory scripts
- `README.md` when it disagrees with the production code

If docs and code disagree, trust the current code first, then fix the docs.

## Architecture Map

- API entrypoint: `backend/main.py`
- Main settings surface: `backend/core/config.py`
- Security middleware and auth rules: `backend/core/security.py`, `backend/routers/auth.py`
- DB manager: `backend/core/database.py`
- Search endpoints: `backend/routers/search.py` (query embeddings go through `backend/services/query_embedding.py`, which applies the model family's query prefix, e.g. for EmbeddingGemma; `EMBEDDING_QUERY_PREFIX` overrides or disables it)
- Chat/session APIs: `backend/routers/chat.py`, `backend/routers/sessions.py`
- Federated/orchestrated agent (legacy): `backend/agent/orchestrator.py`, `backend/agent/strategies/` (plural, legacy `BaseStrategy` registry)
- Strategy OS runtime: `backend/agent/strategy/` (singular), `backend/agent/strategy/nodes/`, `backend/agent/strategy/strategy_runner.py`, `backend/agent/strategy/spec_store.py`
- Strategy OS routers: `backend/routers/strategy_specs.py`, `backend/routers/evaluation.py`, `backend/routers/scheduler.py`
- Strategy OS configs: `backend/config/strategy_specs/`, `backend/config/capabilities/`, `backend/config/answer_contracts/`, `backend/config/tenant_strategy_policies/`
- Strategy OS scheduler & evaluation: `backend/scheduler/scheduler_daemon.py`, `backend/evaluation/runner.py`, `backend/evaluation/composite_scorer.py`
- Synthetic Austrian law-firm test corpus generator: `backend/evaluation/synthetic_corpus/` (`python -m backend.evaluation.synthetic_corpus` or `quellexctl synth`; areas, case seeds, prompts and fee tables are JSON/Jinja data under `data/`), documented in `docs/20-SYNTHETIC_CORPUS.md`; its benchmark telemetry sampler is `resources.py` (GPU, host, containers, Ollama) and the one-page HTML telemetry report is `telemetry_html.py` (`quellexctl synth bench-telemetry`)
- Strategy OS observability: `backend/services/runtime_profiler.py`, `backend/services/activity_tracker.py`, `backend/services/resource_snapshot.py`
- Host telemetry history (superadmin): Windows host agent `installer/host-agent` (.NET 8 service; tests `installer/host-agent.Tests`; install `installer/scripts/Install-HostAgent.ps1`, optional PawnIO driver for CPU temperatures) writing `host_samples`; activity events from `backend/services/activity_events.py` and the pipeline hooks `src/ingestion/telemetry_hooks.py`; store/analysis `backend/services/telemetry_store.py`, `telemetry_analysis.py`, `telemetry_report_html.py`; API `backend/routers/telemetry_history.py` and `GET /api/v1/system/host-stats` (widget `frontend/src/components/HostStatsWidget.tsx`); page `frontend/src/pages/TelemetryHistoryPage.tsx`; `quellexctl telemetry`; documented in `docs/12-TELEMETRY_SYSTEM.md`
- `quellexctl` CLI: `backend/cli/main.py`, `backend/cli/strategy_commands.py`, `backend/cli/eval_commands.py`, `backend/cli/phase6_commands.py`, `backend/cli/bench_commands.py`
- Embedding benchmark & validation checks: `backend/services/embedding_benchmark.py`, `backend/services/embedding_checks.py`, `backend/routers/embedding_benchmark.py`, `quellexctl bench embeddings`, `scripts/benchmark-embeddings.ps1`
- GPU thermal / mixed-placement stress test: `backend/services/gpu_stress.py`, `backend/cli/stress_commands.py`, `backend/config/gpu_stress/`, `quellexctl bench gpu-stress`, `scripts/gpu-stress.ps1`
- Ingestion API: `backend/routers/ingestion.py`
- Ingestion worker: `backend/workers/ingestion_worker.py`
- Ingestion pipeline and chunking: `src/ingestion/` (`SUPPORTED_INGESTION_EXTENSIONS` is the canonical file filter, also behind the API's glob patterns via `_supported_file_patterns()`; readers for the law-firm legacy formats `.rtf` (striprtf), `.dot`/`.dotx`, legacy `.xls` (xlrd), `.mht`, multi-page TIFF, the DOS-era text files `LEGACY_TEXT_EXTENSIONS`, `.wvb`/`.mtt` sniffing, and the `._` AppleDouble skip are documented in `docs/26-FORMAT_EDGE_AUDIT.md`); the Docling models it needs are baked into `backend/Dockerfile.base` under `DOCLING_ARTIFACTS_PATH` (add new docling models there, no runtime download fallback; `docs/15-DOCKER_BUILD_GUIDE.md`)
- Image-only (pure scan) PDFs are OCRed in both ingestion loops; the concurrent loop's pre-check that skipped them is opt-in via `INGEST_SKIP_IMAGE_ONLY_PDF` (`skip_image_only_pdf_enabled` in `src/ingestion/ingest.py`, default off); audited in `docs/26-FORMAT_EDGE_AUDIT.md`
- Email (.msg/.eml) attachment extraction: off by default; `MSG_EXTRACT_ATTACHMENTS` (+ `MSG_ATTACHMENT_MAX_MB`, `MSG_ATTACHMENT_MAX_COUNT`) opens attachments and appends their extracted text (`resolve_msg_attachment_config` / `extract_email_attachment_text` in `src/ingestion/ingest.py`); audited in `docs/26-FORMAT_EDGE_AUDIT.md`
- Ingestion throughput: every run converts on one shared thread pool sized from the Configuration page's performance config (`ingestion_config.performance_config`: `thread_pool_workers`, never below `max_concurrent_files`, max 32; `DocumentIngestionPipeline.configure_executor` / `resolve_thread_pool_workers` in `src/ingestion/ingest.py`); CPU threads per Docling model (`docling_num_threads`) and per Tesseract process (`OCR_OMP_THREAD_LIMIT`, applied through the wrapper from `tesseract_command()` in `src/ingestion/ocr_tesseract.py`) are shared across the pool; Docling accelerator options always come from `docling_accelerator_options()` (a dict there broke every conversion on a GPU); GPU conversions are opt-in through `docker-compose.gpu.yml` (layered via `COMPOSE_FILE`) and capped by `INGEST_GPU_SLOTS` (`resolve_gpu_slots` / `slot_device` / `conversion_device` in `src/ingestion/ingest.py`: the first N pool threads keep their converters on the GPU, ~1 GB VRAM each, the rest on the CPU; lowered to the free VRAM at pool start, a slot that hits CUDA OOM moves to the CPU via `convert_with_cpu_fallback`; per-slot worker processes pin theirs with `assign_conversion_slot`); UI-started jobs run inside the API process, not the worker. Measurements and tuning in `docs/04-EMBEDDING_AND_QUERY_SYSTEM.md`, "Parallel conversions"
- Pre-ingestion estimator: `backend/services/ingestion_estimator.py`, `GET /api/v1/profiles/{key}/ingest/estimate` (in `backend/routers/profiles.py`), `quellexctl ingest estimate` (`backend/cli/ingest_commands.py`), the estimate line in `frontend/src/pages/IngestionManagementPage.tsx` (`ingestionApi.estimate`); documented in `docs/22-DOCUMENT_FOLDERS_AND_SHARES.md`
- Profiles and tenant-aware model selection: `backend/routers/profiles.py`, `profiles.yaml`, `src/profile.py`
- Profile document folders and network shares: `backend/services/document_folders.py` (`DOCUMENT_ROOTS` validation + browsing), `GET /api/v1/profiles/folders/browse`, `frontend/src/components/ServerFolderPicker.tsx`, the whole `./mounts` folder mounted at `/app/mounts` in `docker-compose.yml`, `docker-compose.shares.example.yml` + `installer/scripts/Add-NetworkShare.ps1` (CIFS volumes, loaded by `Start-Services.ps1`/`Stop-Services.ps1`/tray `ServiceManager.cs`); documented in `docs/22-DOCUMENT_FOLDERS_AND_SHARES.md`
- Cloud source providers and registry: `backend/providers/`, `backend/providers/registry.py`
- Backups: `backend/services/backup_service.py`, `backend/routers/backup.py`
- Network access / login enforcement: `backend/core/auth_middleware.py` (`REQUIRE_AUTH`), `gateway/Caddyfile.template` + the `gateway`/`gateway-quellex` Compose services (TLS edge, `BIND_ADDRESS`), `frontend/src/components/StartPage.tsx` + `RequireAuth.tsx` (tenant `startPage` / `requireAuth`), `installer/tray-app/LanNameAdvertiser.cs` + `Branding.cs`, `installer/scripts/Configure-LanAccess.ps1`, `Configure-Uplinks.ps1` (two adapters: metrics, routes, firewall scope), `Export-LanCertificate.ps1`, `Write-TrayConfig.ps1`; documented in `docs/21-LAN_ACCESS.md`
- Frontend API client: `frontend/src/api/client.ts`
- Frontend tenants: `frontend/src/tenants/configs/`
- Frontend tests and MSW setup: `frontend/src/test/`
- Documentation: top-level master spec `01-SYSTEM_BLUEPRINT.md`; numbered blueprint set in `docs/` (`02-SYSTEM_BLUEPRINTS.md` … `26-FORMAT_EDGE_AUDIT.md`). Strategy OS deep-dive is `docs/07-STRATEGY_OS_OVERVIEW.md` through `docs/11-EXPLORATION_AND_SCHEDULER.md`. Non-scan format/edge-case ingestion audit is `docs/26-FORMAT_EDGE_AUDIT.md`.

## Non-Negotiable Rules

1. Prefer production paths over prototype paths.
   If a feature exists in both `backend/` and `src/` or `examples/`, modify the production path unless the task explicitly says otherwise.

2. Keep `AGENTS.md` and `CLAUDE.md` in sync.
   Drift between them creates bad automation behavior.

3. Do not introduce new `motor` usage.
   This repo already has mixed Mongo async clients. For new code, prefer `pymongo.AsyncMongoClient`. MongoDB officially deprecated Motor on May 14, 2025 in favor of PyMongo Async.

4. Do not put secrets in code, committed config, or Vite-exposed env vars.
   `VITE_*` values are bundled client-side. Never store tokens, passwords, or private URLs there.

5. Keep ingestion off the request path.
   Heavy document conversion, embedding generation, and file traversal belong in the ingestion worker or dedicated async/background flows, not synchronous API handlers.

6. Preserve tenant-aware behavior.
   This repo runs RecallHub and Quellex variants. Do not hardcode branding, routes, or feature flags in the frontend when tenant config already exists.

7. Extend typed contracts instead of bypassing them.
   Use Pydantic models for Python request/response boundaries and typed TS interfaces in the frontend API layer.

## Current Development Workflow

### Python environment

Prefer `uv`:

```powershell
uv sync
uv run pytest backend/tests -v
```

Use `uv run` for project commands. `uv` automatically keeps the environment aligned with the lockfile and project metadata.

### Frontend environment

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
npm --prefix frontend run lint
npm --prefix frontend run test:run
npm --prefix frontend run build
```

`frontend/package.json` defines the canonical scripts. `build` also type-checks via `tsc && vite build`. Since 2026-10-23 `tsc` is the native TypeScript 7 compiler (`@typescript/native`, an npm alias of `typescript@7`), while the `typescript` package is an alias of `@typescript/typescript6`: TypeScript 7 ships no JavaScript API yet, and typescript-eslint, the editor and other tooling need the 6.x API. Drop the alias once typescript-eslint supports TypeScript 7.1+. `tsconfig.json` has no `baseUrl` (removed in TS 7).

### Docker dev stack

Main profiles from `docker-compose.yml`:

```powershell
docker compose --profile recallhub up -d
docker compose --profile quellex up -d
docker compose --profile recallhub --profile quellex up -d
docker compose --profile recallhub --profile embedder up -d      # + local llama.cpp embedding server (CPU)
docker compose --profile recallhub --profile embedder-gpu up -d  # + NVIDIA variant (needs NVIDIA Container Toolkit)
```

The Compose project name is pinned (`name: recallhub`) and every service has an explicit `image:` (`recallhub-backend`, `recallhub-ingestion-worker`, `recallhub-frontend`, `recallhub-frontend-quellex`), so container, network and image names do not depend on the checkout folder. Only one folder per machine can own the stack; both tenants are profiles of that one stack. The offline installer (`scripts/INSTALL.ps1`, `scripts/full-backup.ps1`, `installer/scripts/*.ps1`) relies on these names - see `docs/14-OFFLINE_DEPLOYMENT.md` and `docs/19-INSTALL_TEST_POSTMORTEM.md`.

The llama.cpp embedder (`embedder` / `embedder-gpu` profiles) is opt-in in offline packages: `scripts/full-backup.ps1 -IncludeEmbedder [-EmbedderVariant cpu|gpu|both]` exports the server image(s) and the `embedder_models` volume (`scripts/export-embedder-models.ps1`), `INSTALL.ps1` / `full-restore.ps1 -Embedder auto|none|cpu|gpu` seed the volume through `installer/scripts/Import-EmbedderModels.ps1`, and the hardened installer takes `"embedder": { "enabled": true }` in `installer/config.json` (`installer/build.ps1 -IncludeEmbedder`). Details in `docs/04-EMBEDDING_AND_QUERY_SYSTEM.md`, section "Local Embedding Server".

Default local ports:

- MongoDB Atlas Local: `11017`
- RecallHub backend/frontend: `11000` / `11080`
- Quellex backend/frontend: `11001` / `11081`
- Airbyte web/API/builder when enabled: `11020` / `11021` / `11022`
- llama.cpp embedding server when enabled: `11090`
- LAN gateway (Caddy TLS edge, the only ports meant for other computers): Quellex `443` / `80` (`https://quellex.local`), RecallHub `11443` / `11480`

All other ports bind to `BIND_ADDRESS` (default `127.0.0.1`); set `BIND_ADDRESS=0.0.0.0` in `.env` only on dev boxes that need raw-port access (e.g. over Tailscale). The Quellex backend runs with `REQUIRE_AUTH=true`: guests get 401 outside health, tenant config, login and register.

## CRITICAL: Backend Docker Build Commands

For Python code-only changes, use the fast path:

```powershell
.\build-backend.ps1 -All
```

Or:

```powershell
.\build-backend.ps1 -Fast
.\build-backend.ps1 -Worker
```

Only rebuild the shared base image when dependency layers change:

```powershell
.\build-backend.ps1 -Base
.\build-backend.ps1 -Check
```

Do not use `docker compose build --no-cache backend` for normal code changes. It defeats the repo's layered build strategy and is dramatically slower.

For frontend-only Docker changes, rebuild the correct tenant service instead:

```powershell
docker compose --profile recallhub build frontend
docker compose --profile quellex build frontend-quellex
```

## Current Standard Procedures

### 1. Backend feature work

- Add or update Pydantic models first.
- Keep route logic in routers thin; move reusable logic into `backend/services/`, `backend/core/`, or `backend/agent/`.
- Use FastAPI `APIRouter` modules and `include_router` patterns. Do not bloat `backend/main.py`.
- Use `HTTPException` for expected client-facing errors and centralized exception handlers for validation/unexpected server failures.
- For startup/shutdown work, extend the FastAPI lifespan flow in `backend/main.py`; do not add new deprecated startup/shutdown event patterns.

### 2. MongoDB and retrieval work

- Prefer `pymongo.AsyncMongoClient` for new async DB access.
- Keep vector dimensions aligned with the active embedding model and index definition.
- Keep `$vectorSearch` as the first stage when using it.
- Project only the fields you need; do not send embeddings back to clients unless a task explicitly requires it.
- Use metadata pre-filters when the request already scopes the search.
- For ANN vector search, start with `numCandidates >= 20 * limit` and tune from there. The current code often under-requests candidates.
- Keep hybrid search explainable and measurable. The current repo standard is manual Reciprocal Rank Fusion in `backend/routers/search.py`; only replace it with a MongoDB-native alternative after compatibility and recall/latency checks.

### 3. Ingestion work

- Convert documents with Docling first and chunk native `DoclingDocument` objects.
- Prefer Docling native chunkers over ad hoc markdown splitting.
- For embeddings, use the contextualized chunk text, not raw fragment text when richer context is available.
- Batch embedding requests whenever possible.
- Preserve incremental/idempotent ingestion behavior and file-registry semantics.
- Keep long-running file processing in the worker and emit progress/log updates through the existing job collections and streaming endpoints.

### 4. Agent/orchestration work

- Put multi-step reasoning changes in `backend/agent/`, not ad hoc per-route prompt logic.
- Add new domain behavior through strategies and registry wiring, not copy-pasted prompt branches.
- Respect configured timeouts, worker fan-out, and total request budgets from `backend/core/config.py`.
- Prefer provider abstractions and shared configuration over direct vendor-specific calls scattered across routers.
- If you touch prompts, check both database-backed prompt flows and default fallback behavior.

### 5. Frontend work

- Prefer `frontend/src/api/client.ts` for new API calls. Do not spread new raw `fetch()` calls across pages.
- When touching old pages that still use direct fetch logic, prefer migrating toward the central client instead of adding more one-off calls.
- Keep tenant behavior in `frontend/src/tenants/configs/`, `TenantContext`, and feature guards.
- Do not hardcode API base URLs in components when proxy/client helpers already exist.
- Keep auth, retry, timeout, and error behavior consistent with the central API client.
- Any request that bypasses the axios instance (raw `fetch`, `EventSource`, `window.open`, iframe/img/media `src`) must carry the session token itself: use `getAuthHeaders()` from `frontend/src/api/client.ts`, or `?token=` only for SSE endpoints the middleware allows, and hand the browser blob URLs for embeds and downloads. The Quellex backend runs with login enforcement, so anonymous calls get 401.
- Never place secrets in `VITE_*` variables.

### 6. Testing work

- New backend tests belong in `backend/tests/`.
- New frontend tests belong next to the component/page or in `frontend/src/test/` support files.
- Use Vitest + Testing Library + MSW patterns for frontend request mocking.
- Clear or restore mocks between tests.
- Prefer focused tests for the changed surface, then broader regression checks if the change affects shared infrastructure.

## Quality Bar For New Code

### Python

- Type annotations on all public functions and important locals.
- Pydantic models at API and persistence boundaries.
- Google-style docstrings for non-trivial public functions, classes, and modules.
- Avoid `Any` unless unavoidable and justified.
- Prefer small composable functions over giant route handlers.
- Log with actionable context, but never log secrets or full credentials.
- Do not silently swallow exceptions. Translate, log, or re-raise intentionally.

### FastAPI

- Follow the existing lifespan-based resource initialization model.
- Use dependency injection where it reduces duplication or improves testability.
- Keep route responses explicit with `response_model` where appropriate.
- Return 4xx for client mistakes and reserve 5xx for actual server faults.

### MongoDB

- Keep document schema and index assumptions explicit.
- If changing embedding model or dimensions, update all of:
  - `.env.example`
  - `src/settings.py`
  - `backend/core/config.py`
  - index setup flows/scripts
  - profile defaults
  - docs and migration notes
- Do not introduce hidden collection names or index names in random modules; flow them through settings/profile config.

### Frontend

- Keep TypeScript strict and explicit.
- Reuse context/providers/hooks before adding duplicate state containers.
- Prefer stable UI states over optimistic hacks for auth, sessions, sync jobs, and ingestion telemetry.
- When API contracts change, update the frontend types and the backend schema together.

## Repo-Specific Gotchas

- `src/` is not dead code. The ingestion worker imports `src.ingestion.ingest` and `src.profile`.
- `backend/` and `src/` settings overlap. If you change env/config behavior, inspect both `backend/core/config.py` and `src/settings.py`.
- The repo currently mixes PyMongo Async and Motor. New code should move toward PyMongo Async, not deepen the split.
- `backend/providers/registry.py` tries to load some optional providers dynamically. Missing imports can be expected; do not "fix" them by removing extensibility.
- `backend/routers/chat.py` and `backend/routers/sessions.py` both participate in chat behavior. Verify which path the UI actually uses before changing chat flows.
- User roles are two flags on the `users` document: `is_admin` (superadmin) and `is_org_admin` (organisation admin). Guard system configuration with `require_admin`; use `require_org_admin` only for user management, profile access, the team API-key listing and profile edits (`PUT /api/v1/profiles/{key}`: own profiles, name/description/folders only, folders validated against `DOCUMENT_ROOTS`), and keep organisation admins away from superadmin accounts (see `assert_can_manage_target` in `backend/routers/auth.py`).
- Profile folders are container paths. `./mounts` is mounted whole at `/app/mounts` in the backends and the worker, and network shares are attached below `/app/mounts` through `docker-compose.shares.yml`; the backend and the worker must see the same paths or the document viewer falls back to plain text. Ingestion jobs call `ProfileManager.reload()` first, so folder edits need no restart.
- `frontend/src/api/client.ts` is the real frontend contract hub. If you change backend payloads and skip it, the UI will drift.
- `openapi.json` is generated output. Do not hand-edit it.
- Never run `tsc -p frontend/tsconfig.node.json` on its own: that config is `composite` without `noEmit`, so it writes `vite.config.js`, `vite.config.d.ts` and `tsconfig.node.tsbuildinfo` next to `vite.config.ts`, and Vite then loads the stale `.js` first. Delete those files if they appear. `npm run build` only type-checks `tsconfig.json`.
- There is historical config in the repo that uses embedded defaults for secret-like values. Do not repeat that pattern.

## Delivery Checklist

For every non-trivial change:

1. Identify the active production path.
2. Update typed models/contracts first.
3. Implement the feature in the correct layer.
4. Add or update tests near the changed surface.
5. Run the smallest meaningful verification set.
6. If behavior, config, or operations changed, update docs in the same PR.

Minimum verification guidance:

- Backend logic/API: `uv run pytest backend/tests -v`
- Frontend logic/UI: `npm --prefix frontend run test:run`
- Frontend build/type safety: `npm --prefix frontend run build`
- Backend/worker container behavior: `.\build-backend.ps1 -All`

If local tooling is blocked by machine-specific cache/permission issues, document the exact blocker in your final note instead of pretending verification passed.

## External Standards This File Applies

These instructions were aligned to the current official docs for the stack:

- FastAPI lifespan and app structure:
  - https://fastapi.tiangolo.com/advanced/events/
  - https://fastapi.tiangolo.com/tutorial/bigger-applications/
  - https://fastapi.tiangolo.com/tutorial/handling-errors/
- MongoDB vector search and async driver guidance:
  - https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-stage/
  - https://www.mongodb.com/docs/atlas/atlas-search/operators-collectors/vectorSearch/
  - https://www.mongodb.com/docs/drivers/motor/
  - https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/
- Pydantic Settings:
  - https://docs.pydantic.dev/latest/api/pydantic_settings/
  - https://docs.pydantic.dev/2.4/concepts/pydantic_settings/
- Docling chunking:
  - https://docling-project.github.io/docling/concepts/chunking/
  - https://docling-project.github.io/docling/examples/hybrid_chunking/
- uv workflow:
  - https://docs.astral.sh/uv/concepts/projects/sync/
  - https://docs.astral.sh/uv/concepts/projects/run/
- Vite env handling:
  - https://vite.dev/guide/env-and-mode/
- Vitest mocking/network testing:
  - https://vitest.dev/guide/mocking/requests.html
  - https://vitest.dev/guide/mocking

