# Local (single-user) vs. network (multi-user) mode: validation report

Date: 2026-10-11
Machine: DESKTOP-8ESH62J (minisforumm502), Windows 11 Pro 26200
Scope: read-only validation. No code, config, container, firewall or database state was changed.
Side effects of the validation itself: one Docker image pulled (`curlimages/curl:8.10.1`), a handful of read-only HTTP probes and load requests against the running Quellex stack, one untracked report file (this document).

## 1. Summary

| Question | Answer |
|---|---|
| Is a dedicated "local mode" (one user, console only) implemented? | **Partially, as a deployment profile only.** The hardened Inno Setup installer design binds the UI to `127.0.0.1`, hides every other port and writes localhost-only firewall rules. There is no runtime switch in the backend or frontend, and the installer actually used on this PC does not apply any of it. |
| Is a "multi-user network mode" implemented? | **Yes at the application level** (JWT login, users, admin role, registration modes, per-user sessions/folders, profile access matrix, API keys, per-IP rate limiting, reverse proxy with relative API paths). **No** as a safe, switchable deployment mode: large parts of the API answer without authentication, MongoDB is published on all interfaces without enforced auth, there is no TLS, and the `ENABLE_REMOTE_ACCESS` flag is stored but never enforced. |
| Is the stack as deployed on this PC in local mode or network mode? | **Network-exposed by default.** Backend, frontend and MongoDB listen on `0.0.0.0` (all interfaces). The Windows Firewall has no rule for the Docker port forwarder, so LAN (Wi-Fi, Public profile) clients are most likely blocked by the firewall default, while Tailscale peers are most likely admitted by the existing `Tailscale-In` allow rules. This could not be confirmed from a second machine (none reachable). |
| Is the hardware viable? | **Yes for the web/API tier, with a clear GPU limit for the LLM tier.** 24-core CPU, 256 GB RAM, 3.7 TB NVMe. The RTX PRO 2000 has 16 GB VRAM, which is not enough for the configured `gemma4:26b` orchestrator; that model is the bottleneck for both modes. |

## 2. What the code actually implements

### 2.1 "Local mode" artifacts (hardened installer design)

| Artifact | What it does |
|---|---|
| `installer/config.json` | `security.bindHost = 127.0.0.1`, `features.enableRemoteAccess = false`, CORS origins limited to localhost. |
| `installer/scripts/New-BuildConfig.ps1` | Generates `docker-compose.hardened.yml` (frontend published as `127.0.0.1:11080:80`, backend/MongoDB/Ollama on an `internal: true` network with no published ports) and `hardened.env` (`BIND_HOST`, `ENABLE_REMOTE_ACCESS`, `EXPOSE_API_DOCS=false`). |
| `installer/config/docker-compose.hardened.yml` | The generated file. Only port on the host: `127.0.0.1:11080`. |
| `installer/scripts/Configure-Firewall.ps1` | Allows TCP 11080 from `127.0.0.1` only, blocks 11000/11017/11434 inbound, blocks outbound 80/443 from `wsl.exe`. Requires admin. |
| `installer/setup.iss` | Runs `Configure-Firewall.ps1` as a post-install step. |
| `installer/tray-app/*.cs` | WebView2 window hard-wired to `http://localhost:<frontend port>`; health polling against localhost. This is the "console" UI. |

Gaps in the local-mode design:

- `ENABLE_REMOTE_ACCESS` is only read by `GET /api/v1/admin/config` and written by `POST /api/v1/admin/config` (`backend/routers/admin.py:245`, `:283`). Nothing in `backend/`, `src/`, nginx or compose changes behavior based on it. The frontend does not reference it at all.
- `BIND_HOST` is consumed only by the hardened compose generator. The main `docker-compose.yml` has no bind-host variable; it always publishes `0.0.0.0`.
- The backup/restore installer (`scripts/INSTALL.ps1`, the path documented in `docs/14-OFFLINE_DEPLOYMENT.md` and `docs/19-INSTALL_TEST_POSTMORTEM.md`, and the one that produced the running stack in `C:\RecallHub-Verify`) contains zero references to the firewall script or to `bindHost`.
- "Single user" is not a distinct runtime mode. The tray window still shows the normal login page; there is no auto-login, kiosk lock or local-account shortcut. One person simply uses the multi-user system through a localhost window.

### 2.2 Multi-user mode (application level)

| Capability | Where | Status |
|---|---|---|
| Email/password login, JWT (HS256, 7-day expiry) | `backend/routers/auth.py` | Implemented. Secret must be set; `backend/core/security.py` refuses the default in production. Live `.env` has a 64-char non-default secret. |
| Registration modes `open` / `invite` / `closed` | `backend/core/security.py:349`, `auth.py:330` | Implemented. **Live stack has `REGISTRATION_MODE` unset, so it defaults to `open`.** |
| Admin role, user CRUD, enable/disable, role change | `auth.py:626-1010`, `admin.py:430-480` | Implemented. Live DB `rag_test_law` has 6 users, 2 admins. |
| Per-user ownership of chat sessions and folders | `backend/routers/sessions.py` (33 `user_id` references) | Implemented via optional auth: logged-in users see their own, anonymous callers see ownerless items. |
| Per-profile access matrix | `auth.py:648-760` | Implemented. |
| API keys | `auth.py:1010-1150` | Implemented. |
| Per-IP sliding-window rate limiter, brute-force lockout | `backend/core/security.py` | Implemented, in-memory, single-process. 32 unit tests pass. |
| Global agent concurrency caps | `backend/core/config.py:118-126` | 10 orchestrators / 20 workers across all users. |
| Reverse proxy, host-agnostic UI | `frontend/nginx.conf`, `frontend/src/api/client.ts:4` | UI calls `/api/v1` relatively; nginx proxies to the backend container. Works from any IP or hostname without rebuild. |
| App server | container command | `uvicorn` with a single worker process plus a 32-thread executor. |

### 2.3 Endpoints that answer without authentication (verified live)

`backend/routers/search.py` has no auth dependency at all. The OpenAPI document of the running backend lists 172 operations with a security scheme and 146 without. Live probes from a container with no token:

| Request | Result |
|---|---|
| `POST /api/v1/search/text` (valid body) | 200, returns chunk text, titles and source paths from the law corpus |
| `GET /api/v1/ingestion/documents?limit=2` | 200, 20 documents of 508 total |
| `GET /api/v1/ingestion/jobs` | 200, 6 jobs |
| `GET /api/v1/chat/conversations` | 200 |
| `GET /api/v1/sessions` | 200 (ownerless sessions and folders) |
| `GET /docs` (Swagger UI) | 200 |
| `GET /api/v1/profiles` | 401 (protected, as expected) |
| `GET /api/v1/admin/config` | 404 (router mounted elsewhere; not probed further) |

Unauthenticated `DELETE` and `POST` ingestion operations (jobs, failed documents, logs, rebuild-metadata, pause/cancel) also appear without a security scheme in OpenAPI. They were **not** called.

### 2.4 Database exposure

- `rag-mongodb` publishes `0.0.0.0:11017`.
- `mongosh` inside the container reports `security.transitionToAuth: true` and zero authenticated users on the connection, yet listing databases and reading `users` succeeded. In transition mode the server accepts unauthenticated clients. Any host that can reach port 11017 can read and write every tenant database.

## 3. This machine

| Component | Measured |
|---|---|
| CPU | Intel Core Ultra 9 285HX, 24 cores / 24 threads, 2.8 GHz base |
| RAM | 255.7 GB total, 204.3 GB free (4 × 64 GB DDR5-5600) |
| Storage | C: 3,725 GB NVMe (3,250 GB free); second 931 GB NVMe present |
| GPU | NVIDIA RTX PRO 2000 Blackwell, 16,311 MiB VRAM, driver 597.16, 70 W power cap. At test time 10,966 MiB in use (Ollama had `gemma4:12b` loaded) |
| Docker Desktop VM | 23 CPUs, 23.5 GB RAM; MongoDB alone uses 11.9 GB (50 %) |
| Ollama | 0.40.x, bound to `127.0.0.1:11434` only; models e2b, e4b, 12b, 26b pulled |
| Network | Wi-Fi 192.168.4.190 (Public profile), Tailscale 100.126.205.57 (Private profile) |

### 3.1 Running stack (Compose project `recallhub` from `C:\RecallHub-Verify`)

| Container | Host binding | Notes |
|---|---|---|
| rag-backend-quellex | `0.0.0.0:11001`, `[::]:11001` | healthy, TENANT_ID=quellex, DB `rag_test_law` |
| rag-frontend-quellex | `0.0.0.0:11081` | nginx, `SERVER_DOMAINS=test-0-8-7.quellex.at` |
| rag-mongodb | `0.0.0.0:11017` | Atlas Local 8.0 |
| rag-ingestion-worker | none | healthy |

The RecallHub tenant profile is not running. Ports are owned on the Windows side by `com.docker.backend.exe` (IPv4/IPv6 any) and `wslrelay.exe` (`::1`).

LLM configuration of the live stack: `.env` says OpenAI `gpt-5.2` and `text-embedding-3-large` (3072 dims), but the database-backed `llm_config` overrides the orchestrator to `ollama/gemma4:26b` and the worker to `ollama/gemma4:e4b` (confirmed in backend startup log). Embeddings stay on OpenAI, so even "local mode" on this stack needs internet for search and ingestion.

## 4. Tests performed

### 4.1 Reachability

| Path | Result |
|---|---|
| Host loopback: `localhost:11001/health`, `/api/v1/system/health`, `localhost:11081/` | 200 |
| Host via its own LAN and Tailscale IPs | 200 (loopback-routed, does not cross the firewall) |
| Container (WSL2 VM) to `192.168.4.190:11001`, `:11081`, `100.126.205.57:11001`, `192.168.4.190:11017` | all reachable |
| Container to `192.168.4.190:11434` (Ollama) | refused (bound to 127.0.0.1 only; correct) |

Caveat: the Hyper-V firewall for WSL has `LoopbackEnabled = True`, so VM-to-host traffic bypasses host firewall rules. These probes prove the services listen on all interfaces; they do not prove an external client gets through.

Windows Firewall state: all three profiles enabled, default inbound Block. Of 309 inbound rules, none mention `com.docker`, `wslrelay`, "Docker Desktop" or local port 11001. `Tailscale-In` (program Any, profiles Domain/Private) is enabled, and the Tailscale adapter is on the Private profile. Expected consequence: Wi-Fi LAN clients are blocked, tailnet clients are admitted to 11001/11081/11017. Not verified from a second host: `uh125pro` is reachable over Tailscale but this PC's SSH key is not authorized there, and Windows Sandbox is not installed.

### 4.2 Concurrency / load (read-only endpoints, from a container)

| Target | Requests × parallel | Success | p50 | p95 | max |
|---|---|---|---|---|---|
| Backend `/api/v1/system/health` (touches MongoDB) | 200 × 32 | 200/200 | 35 ms | 46 ms | 60 ms |
| Frontend `/` via nginx | 100 × 20 | 100/100 | 7 ms | 9 ms | 10 ms |
| Frontend proxy `/api/v1/system/health` | 100 × 20 | 100/100 | 21 ms | 28 ms | 33 ms |

Backend container stayed below 0.3 % CPU; MongoDB rose to about 11 % CPU. The API/web tier has ample headroom for tens of simultaneous users on this box.

Not load-tested: chat/agent requests. They create sessions and messages in the database and would load `gemma4:26b`, which conflicts with the "change nothing" constraint.

### 4.3 Automated tests

| Suite | Result |
|---|---|
| `uv run pytest backend/tests/test_security.py` | 32 passed (rate limiter, JWT secret validation, registration modes, client IP handling) |
| `npm --prefix frontend run test:run -- LoginPage AuthContext` | 30 passed (login, register, 401/500 handling, auth context) |

## 5. Viability assessment on this hardware

### Local single-user mode

Viable. CPU, RAM and disk are far above the installer minimums (8 GB RAM / 10 GB disk). The one real constraint is the GPU:

- `gemma4:26b` (Q4_K_M, 17 GB on disk, about 18 GB resident) does not fit in 16 GB VRAM. Ollama offloads part of it to CPU; earlier measurements on this machine put fast-mode answers at about one minute and thinking mode much slower. The same model as a llama.cpp 26B-A4B Q4_0 build fits at 15.3 GB and runs about 67 tokens/s, or 28 tokens/s with experts in RAM across 4 clients (see `GPU_CPU_BENCHMARK_DESKTOP-8ESH62J_2026-10-10.md`).
- `gemma4:e4b` (8 B, Q4_K_M) and `gemma4:12b` fit comfortably and are the practical choice for responsive single-user chat.
- Under sustained GPU load the card sits at its 70 W cap and 94-95 °C; no thermal throttling flagged.

### Multi-user network mode

Viable for the web/API/database tier (measured above), with these limits:

- LLM throughput is shared. With Ollama and no `OLLAMA_NUM_PARALLEL` set, concurrent chat requests are effectively serialized per model; with the 26B orchestrator every additional user waits roughly a full generation. Realistic concurrent chat capacity on this GPU is 2-4 users with e4b/12b-class models, 1 user with 26B.
- The Docker VM is capped at 23.5 GB and MongoDB already uses half of it; with 256 GB on the host this cap can be raised, which would help the 432k-chunk `rag_parhelion` database.
- Embeddings and the `.env` default LLM are OpenAI, so network mode is not air-gapped on this stack.

### Security blockers before exposing network mode

1. Unauthenticated search, document listing, ingestion job listing, conversation listing and Swagger UI (section 2.3). Any client that reaches port 11001 or 11081 can read corpus text.
2. MongoDB published on all interfaces in `transitionToAuth` mode (section 2.4).
3. `REGISTRATION_MODE` unset, so self-registration is open.
4. No TLS; JWT bearer tokens and passwords travel in clear text. The installer's `sessionCookieSecure=true` has no effect on the running stack.
5. `ENABLE_REMOTE_ACCESS` and `BIND_HOST` are not wired to anything in the main compose stack; the "local only" promise rests entirely on a firewall script that the active installer does not run.
6. Rate limiting is in-memory and per process; fine for one uvicorn worker, not shared across workers or restarts.

## 6. Addendum 2026-10-14: re-check after the auth/gateway deployment

A separate session deployed a localhost-bind plus HTTPS gateway plus auth-required change to `C:\RecallHub-Verify` (`backend/core/auth_middleware.py`, `BIND_ADDRESS` in compose, Caddy gateway for `https://quellex.local`). Independent read-only re-check from this session:

| Check | Before (2026-10-11) | After (2026-10-14) |
|---|---|---|
| Host bindings of 11001 / 11081 / 11017 | `0.0.0.0` and `[::]` | `127.0.0.1` only (`com.docker.backend`) |
| Published gateway | none | `rag-gateway-quellex` on `0.0.0.0:80` and `:443` |
| Raw backend from a container via LAN IP, Mongo via Tailscale IP | reachable | connection refused |
| Through the gateway without a token: `POST /api/v1/search/text`, `GET /api/v1/ingestion/documents`, `/api/v1/ingestion/jobs`, `/api/v1/chat/conversations`, `/api/v1/sessions`, `/docs`, `/openapi.json` | 200 | 401 |
| `GET /api/v1/system/health`, `GET /api/v1/tenant/config` through the gateway | 200 | 200 (intentionally public) |

Still open after the deployment:

- MongoDB itself still runs in `transitionToAuth` mode with no enforced credentials. It is now reachable only from this host, but any local process can read and write every database.
- `REGISTRATION_MODE` remains unset (open) unless the deployment set it; not re-checked.
- The gateway publishes 80/443 on all interfaces. Whether LAN or Tailscale clients get through still depends on the Windows Firewall; the new `Configure-LanAccess.ps1` needs an elevated run and was not executed or verified here. A container probe to `https://192.168.4.190/` with a `quellex.local` Host header failed at the TLS handshake (certificate is issued for the name, not the IP), which is expected behaviour, not a security gap.
- The RecallHub tenant profile was not part of this check.

## 7. Conclusion

- The product has the building blocks for both modes: a localhost-only hardened deployment profile with a tray window for one person at the console, and a genuine multi-user application with accounts, roles, ownership and rate limiting.
- Neither mode exists as a selectable switch. "Local mode" is a different installer output that is not what runs on this PC, and "network mode" is simply the default compose stack listening on every interface.
- On this exact machine the API, web and database tiers handle concurrent load without strain. The LLM tier is the limiter: the configured 26B orchestrator exceeds the 16 GB GPU, so responsive single-user use needs a smaller model and multi-user chat is realistically 2-4 concurrent users.
- Exposing the stack to a network today would publish corpus content and the database to anyone who can reach the ports. The Windows Firewall, not the application, is currently the only barrier, and it already admits Tailscale peers.
