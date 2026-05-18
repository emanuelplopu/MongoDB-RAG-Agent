# RecallHub Development Instructions

Last reviewed: 2026-05-18

`AGENTS.md` and `CLAUDE.md` must stay functionally identical. Update both in the same change.

## What This Repo Is Now

RecallHub is no longer just the original `examples/` RAG prototype. The active product is:

- `backend/`: production FastAPI API, orchestration, providers, services, auth, backups, cloud sync, search, and admin endpoints.
- `frontend/`: production React 18 + TypeScript + Vite 6 UI with tenant-aware branding and feature gating.
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
- Search endpoints: `backend/routers/search.py`
- Chat/session APIs: `backend/routers/chat.py`, `backend/routers/sessions.py`
- Federated/orchestrated agent (legacy): `backend/agent/orchestrator.py`, `backend/agent/strategies/` (plural, legacy `BaseStrategy` registry)
- Strategy OS runtime: `backend/agent/strategy/` (singular), `backend/agent/strategy/nodes/`, `backend/agent/strategy/strategy_runner.py`, `backend/agent/strategy/spec_store.py`
- Strategy OS routers: `backend/routers/strategy_specs.py`, `backend/routers/evaluation.py`, `backend/routers/scheduler.py`
- Strategy OS configs: `backend/config/strategy_specs/`, `backend/config/capabilities/`, `backend/config/answer_contracts/`, `backend/config/tenant_strategy_policies/`
- Strategy OS scheduler & evaluation: `backend/scheduler/scheduler_daemon.py`, `backend/evaluation/runner.py`, `backend/evaluation/composite_scorer.py`
- Strategy OS observability: `backend/services/runtime_profiler.py`, `backend/services/activity_tracker.py`, `backend/services/resource_snapshot.py`
- `quellexctl` CLI: `backend/cli/main.py`, `backend/cli/strategy_commands.py`, `backend/cli/eval_commands.py`, `backend/cli/phase6_commands.py`
- Ingestion API: `backend/routers/ingestion.py`
- Ingestion worker: `backend/workers/ingestion_worker.py`
- Ingestion pipeline and chunking: `src/ingestion/`
- Profiles and tenant-aware model selection: `backend/routers/profiles.py`, `profiles.yaml`, `src/profile.py`
- Cloud source providers and registry: `backend/providers/`, `backend/providers/registry.py`
- Backups: `backend/services/backup_service.py`, `backend/routers/backup.py`
- Frontend API client: `frontend/src/api/client.ts`
- Frontend tenants: `frontend/src/tenants/configs/`
- Frontend tests and MSW setup: `frontend/src/test/`
- Documentation: top-level master spec `01-SYSTEM_BLUEPRINT.md`; numbered blueprint set in `docs/` (`02-SYSTEM_BLUEPRINTS.md` … `18-RESPONSE_QUALITY_IMPROVEMENTS.md`). Strategy OS deep-dive is `docs/07-STRATEGY_OS_OVERVIEW.md` through `docs/11-EXPLORATION_AND_SCHEDULER.md`.

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

`frontend/package.json` defines the canonical scripts. `build` also type-checks via `tsc && vite build`.

### Docker dev stack

Main profiles from `docker-compose.yml`:

```powershell
docker compose --profile recallhub up -d
docker compose --profile quellex up -d
docker compose --profile recallhub --profile quellex up -d
```

Default local ports:

- MongoDB Atlas Local: `11017`
- RecallHub backend/frontend: `11000` / `11080`
- Quellex backend/frontend: `11001` / `11081`
- Airbyte web/API/builder when enabled: `11020` / `11021` / `11022`

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
- `frontend/src/api/client.ts` is the real frontend contract hub. If you change backend payloads and skip it, the UI will drift.
- `openapi.json` is generated output. Do not hand-edit it.
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

