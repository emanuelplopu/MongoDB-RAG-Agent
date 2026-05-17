# RecallHub System Blueprint

## Part 1: Functionality Overview

### Core Platform
- **1. Authentication & User Management** — JWT-based auth, user registration (open/invite/closed), API key management, admin user controls, profile-based access control
- **2. Multi-Tenant Profile System** — Project isolation via profiles with separate databases, collections, document folders, model configs, and cloud source associations
- **3. Dual-LLM Agent Architecture** — Orchestrator-worker pattern with a thinking model (planning/evaluation/synthesis) and fast workers (search execution/summarization)
- **4. Federated Search Engine** — Multi-database search across profile, personal, cloud-shared, and cloud-private data sources with access control enforcement
- **5. Search Subsystem** — Semantic (vector), full-text, and hybrid search using MongoDB Atlas Vector Search, Atlas Search, and Reciprocal Rank Fusion (RRF)
- **6. Chat & Conversation Management** — Session-based chat with message history, folder organization, model selection, streaming SSE responses, token/cost tracking
- **7. Document Ingestion Pipeline** — Multi-phase ingestion (discovery, processing, chunking, embedding, storage) with job persistence, graceful shutdown, and file classification
- **8. Ingestion Queue & Scheduling** — Priority-based queue, scheduled ingestion (hourly/daily/weekly/monthly), selective retry by failure category, file type filtering
- **9. Embedding Provider System** — Multi-provider embedding (OpenAI, Google Gemini, Voyage AI, Ollama) with task-type optimization, adjustable dimensions, cost tracking
- **10. LLM Provider System** — Multi-provider LLM support (OpenAI, Google Gemini, Anthropic Claude, Ollama, OpenAI-compatible) via LiteLLM with per-provider API key management
- **11. Agent Strategy System** — Pluggable strategy pattern for domain-specific behavior (enhanced, legacy, software_dev, legal, HR) with auto-detection and A/B metrics
- **12. Cloud Source Integration** — OAuth2-based connections for Google Drive, Dropbox, WebDAV, with delta sync, file caching (LFU eviction), and folder browsing
- **13. Email Integration** — Email sync via IMAP, Gmail API, and Outlook API with attachment extraction and incremental sync
- **14. Airbyte Connector Integration** — Confluence, Jira, and generic API source integration via Airbyte for structured data ingestion
- **15. Prompt Template Management** — Versioned prompt templates with categories, tool definitions, activation control, and live testing
- **16. Model Version Registry** — Comprehensive model catalog with pricing, capabilities, parameter mapping, and compatibility metadata
- **17. Credential Vault** — Fernet-based encryption at rest for OAuth tokens and API keys with PBKDF2 key derivation and key rotation
- **18. Backup & Restore System** — Full and incremental backups, checkpoints, configurable retention policies, post-ingestion auto-backup, restore modes (full/merge/incremental)
- **19. File Registry & Classification** — File tracking with SHA256 change detection, classification (normal/image_only_pdf/no_chunks/timeout/error/pending), retry metadata
- **20. Search Index Management** — MongoDB Atlas Vector Search and Text Search index creation, status monitoring, and configuration
- **21. System Administration** — Health dashboard, detailed diagnostics, configuration management, log streaming, system statistics
- **22. Security Middleware** — Rate limiting (sliding window per endpoint category), security headers (CSP, X-Frame-Options), request timeouts, brute-force lockout
- **23. Browser Tool** — Web page fetching and content extraction for real-time information retrieval during agent conversations
- **24. Embedding Benchmark System** — Provider comparison benchmarks with latency, quality, and cost analysis across multiple providers
- **25. Local LLM Management** — Ollama model discovery, pull, deletion, testing, and configuration for fully offline operation
- **26. Update Service** — Offline update package management with integrity verification (SHA256 + Ed25519), pre-update backup, database migration, and rollback
- **27. Frontend Application** — React + TypeScript SPA with Material-UI/Tailwind, i18n, chat interface, admin panels, search UI, ingestion management, profile switching
- **28. Telemetry System** — Full interaction trace capture (LLM calls, search operations, tool executions), dual-mode PII protection (protected + raw for trusted partners), triple-engine pseudonymizer (regex + spaCy + Presidio), admin API for runtime configuration, JSONL storage with configurable retention, standalone Electron viewer app (`tools/telemetry-viewer/`)

---

## Part 2: Detailed Functional Specifications

---

### 1. Authentication & User Management

**Purpose:** Secure user identity verification, session management, and programmatic access control.

**Data Model:**
- `User`: `{id: UUID, email: string (unique, lowercase), name: string, password_hash: string (bcrypt, 12 rounds), created_at: datetime, updated_at: datetime, is_active: bool, is_admin: bool}`
- `APIKey`: `{id: UUID, user_id: string, name: string, key_hash: SHA256, key_prefix: string (first 8 chars), created_at: datetime, last_used_at: datetime|null, expires_at: datetime|null, is_active: bool, scopes: list["read","write"]}`
- `ProfileAccess`: `{user_id: string, profile_key: string, granted_at: datetime, granted_by: string}`

**Storage:** MongoDB collections `users`, `api_keys`, `profile_access` in the active database.

**Authentication Flow:**
1. User submits email + password to `POST /api/v1/auth/login`
2. Password verified against bcrypt hash
3. JWT issued with `HS256`, payload `{sub: user_id}`, expiry 7 days
4. Token sent as `Authorization: Bearer <token>` on subsequent requests
5. API key alternative: `X-API-Key` header with `rh_` prefixed 64-char hex key

**Registration Modes (env `REGISTRATION_MODE`):**
- `open`: Anyone can register
- `invite`: Requires valid invite code (env `INVITE_CODE` or `INVITE_CODES` comma-separated)
- `closed`: Only admins create users via `POST /api/v1/auth/users/create`

**API Endpoints:**
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/auth/register` | None | Register new user |
| POST | `/api/v1/auth/login` | None | Login, returns JWT |
| GET | `/api/v1/auth/me` | User | Get current user info |
| POST | `/api/v1/auth/logout` | User | Logout (client discards token) |
| PUT | `/api/v1/auth/me` | User | Update own profile |
| PUT | `/api/v1/auth/me/password` | User | Change password |
| GET | `/api/v1/auth/users` | Admin | List all users |
| POST | `/api/v1/auth/users/create` | Admin | Create user |
| PUT | `/api/v1/auth/users/{user_id}` | Admin | Update user |
| PUT | `/api/v1/auth/users/{user_id}/status` | Admin | Activate/deactivate |
| DELETE | `/api/v1/auth/users/{user_id}` | Admin | Delete user + access |
| GET | `/api/v1/auth/access-matrix` | Admin | Profile access matrix |
| POST | `/api/v1/auth/access` | Admin | Grant/revoke profile access |
| GET | `/api/v1/auth/api-keys` | User | List own API keys |
| POST | `/api/v1/auth/api-keys` | User | Create API key (key returned once) |
| DELETE | `/api/v1/auth/api-keys/{key_id}` | User | Revoke API key |
| PUT | `/api/v1/auth/api-keys/{key_id}/toggle` | User | Enable/disable key |
| GET | `/api/v1/auth/admin/api-keys` | Admin | List all system API keys |

**Integration Points:**
- `get_current_user(request)` — dependency injector, returns `UserResponse | None`
- `require_auth(request)` — dependency, raises 401 if not authenticated
- `require_admin(request)` — dependency, raises 403 if not admin
- `user_has_profile_access(request, user_id, profile_key)` — checks access rights
- `get_user_accessible_profiles(request, user_id)` — returns list of profile keys

---

### 2. Multi-Tenant Profile System

**Purpose:** Isolate data, configuration, and document sources per project/client.

**Data Model:**
- `ProfileConfig`: `{name: string, description: string|null, owner_user_id: string|null, documents_folders: list[string], database: string (default "rag_db"), collection_documents: string (default "documents"), collection_chunks: string (default "chunks"), vector_index: string (default "vector_index"), text_index: string (default "text_index"), embedding_model: string|null, llm_model: string|null, orchestrator_model: string|null, orchestrator_provider: string|null, worker_model: string|null, worker_provider: string|null, embedding_provider: string|null, airbyte: AirbyteConfig|null, cloud_sources: list[CloudSourceAssociation]}`

**Storage:** `profiles.yaml` file on disk + in-memory ProfileManager singleton. Active profile persisted to database.

**Profile Switching Mechanism:**
1. Client calls `POST /api/v1/profiles/switch` with `{profile_key: string}`
2. `DatabaseManager.switch_database()` changes active MongoDB database and collection references
3. All subsequent search/ingestion/chat operations use the switched database
4. Active profile key persisted for restart recovery

**API Endpoints:**
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/v1/profiles/` | User | List all profiles + active |
| POST | `/api/v1/profiles/` | Admin | Create new profile |
| GET | `/api/v1/profiles/{key}` | User | Get profile details |
| PUT | `/api/v1/profiles/{key}` | Admin | Update profile |
| DELETE | `/api/v1/profiles/{key}` | Admin | Delete profile |
| POST | `/api/v1/profiles/switch` | User | Switch active profile |

**Integration Points:**
- `get_profile_manager(profiles_path)` — returns ProfileManager singleton
- `ProfileManager.active_profile` — current ProfileConfig
- `ProfileManager.active_profile_key` — current profile key string
- Database name derived from `ProfileConfig.database` field
- Document folders from `ProfileConfig.documents_folders` list

---

### 3. Dual-LLM Agent Architecture

**Purpose:** Separate expensive reasoning (orchestrator) from fast execution (workers) for cost-efficient, high-quality responses.

**Components:**

**FederatedAgent (coordinator.py):**
- Entry point class, instantiated per request
- Constructor: `FederatedAgent(config: AgentModeConfig, federated_search: FederatedSearch, strategy: BaseStrategy, strategy_id: string)`
- Main method: `process(user_message, user_id, user_email, session_id, conversation_history, active_profile_key, active_profile_database, accessible_profile_keys, on_event) -> Tuple[response_text, AgentTrace]`
- Determines execution mode: THINKING (full pipeline) or FAST (direct search + synthesis)
- Cleans up via `cleanup()` after processing

**Orchestrator (orchestrator.py):**
- Uses the thinking/planning model (default: gpt-4o)
- Phase methods:
  - `analyze(user_message, conversation_history) -> dict` — extracts intent, entities, complexity, source needs
  - `plan(analysis, available_sources) -> AgentPlan` — creates task list with strategy (parallel/sequential/iterative)
  - `evaluate(plan, results, iteration) -> EvaluationDecision` — assesses result quality, decides refinement
  - `synthesize(user_message, results) -> string` — generates final response from gathered data
- Supports strategy-based prompt customization via `strategy.get_X_prompt()` methods

**WorkerPool (worker_pool.py):**
- Uses the fast model (default: gemini-2.0-flash-exp)
- `execute_tasks(tasks, user_id, ..., on_task_complete) -> List[WorkerResult]`
- Task types: `SEARCH_PROFILE`, `SEARCH_CLOUD`, `SEARCH_PERSONAL`, `SEARCH_ALL`, `WEB_SEARCH`, `BROWSE_WEB`, `SUMMARIZE`, `REFINE_QUERY`
- Parallel execution with semaphore limiting (`max_workers` default 4)
- Dependency resolution: tasks wait for `depends_on` task IDs before executing
- Quality assessment: EXCELLENT (5+ results, avg score >0.8), GOOD (3+ results, avg >0.5), PARTIAL, EMPTY

**Execution Modes (AgentMode enum):**
- `AUTO`: Heuristic-based — uses THINKING if query contains complexity indicators ("analyze", "compare", "why", "how does") or length > threshold
- `THINKING`: Full orchestrator-worker pipeline (analyze → plan → execute → evaluate → refine → synthesize)
- `FAST`: Direct search + single-pass synthesis, no orchestration

**AgentModeConfig:**
```
{mode: AgentMode, orchestrator_model: string, worker_model: string, max_iterations: int (default 3), parallel_workers: int (default 4), show_full_trace: bool, auto_thinking_threshold: int (default 20), strategy: StrategySelection, strategy_override: string|null}
```

**AgentTrace (full execution record):**
```
{id: UUID, orchestrator_model: string, worker_model: string, mode: AgentMode, user_id: string, session_id: string, orchestrator_steps: list[OrchestratorStep], worker_steps: list[WorkerStep], iterations: int, all_documents: list[DocumentReference], all_web_links: list[WebReference], initial_plan: AgentPlan, evaluation_history: list[EvaluationDecision], timing: {total_ms, orchestrator_ms, worker_ms}, tokens: {total, orchestrator, worker}, cost_usd: float}
```

**Early Exit Conditions:**
- 2 consecutive iterations with zero results → exit
- Excellent results on first iteration (2+ excellent or 3+ good with avg score >0.75) → skip to synthesis
- Evaluation confidence ≥ 0.80 with "sufficient" decision → exit
- Relevance filtering: results below `MIN_RELEVANCE_SCORE = 0.3` are discarded before synthesis

---

### 4. Federated Search Engine

**Purpose:** Search across multiple isolated databases with access control, combining results transparently.

**FederatedSearch class (federated_search.py):**
- Constructor: `FederatedSearch(mongo_client: AsyncMongoClient)`
- Uses a shared MongoDB async client (PyMongo Async) across all database searches

**Data Source Types (DataSourceType enum):**
- `PROFILE` — shared profile documents (database from profile config)
- `PERSONAL` — user's personal data (database: `user_rag_{email_prefix}`)
- `CLOUD_PRIVATE` — user's private cloud storage (database: `cloud_{email_prefix}`)
- `CLOUD_SHARED` — shared cloud storage per profile (database: `cloud_shared_{profile_key}`)

**Access Control:**
- `get_accessible_sources(user_id, user_email, active_profile_key, active_profile_database, accessible_profile_keys) -> List[DataSource]`
- Profile sources only if user has active profile access
- Personal/cloud-private restricted to owner only (matched by user_id)
- Cloud-shared available to all users with profile access

**Search Method:**
- `search(query, user_id, user_email, sources, ..., match_count, search_type, strategy) -> Tuple[List[DocumentReference], metadata_dict]`
- Generates query embedding once, reuses across all source searches
- Parallel `asyncio.gather()` across all accessible sources
- Per-database search: runs both vector and text search pipelines
- RRF scoring with `k=60` constant, optionally customized by strategy
- Deduplication by chunk ID, ranked by final RRF score

**DocumentReference output:**
```
{id: chunk_id, document_id: string, title: string, source_type: DataSourceType, source_database: string, excerpt: string (max 500 chars), full_content: string|null, similarity_score: float, metadata: dict}
```

---

### 5. Search Subsystem

**Purpose:** Provide three search modalities over MongoDB Atlas.

**Semantic Search (`POST /api/v1/search/semantic`):**
- Input: `{query: string, match_count: int (1-50, default 10)}`
- Pipeline: `$vectorSearch` (index: `vector_index`, path: `embedding`, numCandidates: 100) → `$lookup` (join documents) → `$project` (chunk_id, content, similarity via `$meta: vectorSearchScore`, metadata, document_title, document_source)
- Returns: `SearchResponse` with `SearchResultItem` list

**Full-Text Search (`POST /api/v1/search/text`):**
- Pipeline: `$search` (index: `text_index`, path: `content`, fuzzy: `{maxEdits: 2, prefixLength: 3}`) → `$limit` → `$lookup` → `$project`
- Score via `$meta: searchScore`

**Hybrid Search (`POST /api/v1/search/hybrid`):**
- Runs both semantic and text search in parallel with `fetch_count = match_count * 2`
- Reciprocal Rank Fusion: `score(d) = Σ 1/(k + rank)` where `k=60`
- Scores accumulated across both result sets, sorted descending, top `match_count` returned

**Unified Endpoint (`POST /api/v1/search/` or `/api/v1/search`):**
- Routes to semantic/text/hybrid based on `search_type` field in `SearchRequest`

**SearchRequest schema:**
```
{query: string (1-5000 chars), search_type: "semantic"|"text"|"hybrid" (default "hybrid"), match_count: int (1-50, default 10), text_weight: float (0-1, default 0.3)}
```

**SearchResultItem schema:**
```
{chunk_id: string, document_id: string, document_title: string, document_source: string, content: string, similarity: float, metadata: dict}
```

---

### 6. Chat & Conversation Management

**Purpose:** Persistent chat sessions with AI responses powered by the federated agent.

**Data Model:**
- `Session`: `{_id: UUID, user_id: string, title: string, model: string, folder_id: string|null, pinned: bool, archived: bool, messages: list[Message], stats: {total_messages, total_tokens, total_cost_usd}, created_at: datetime, updated_at: datetime}`
- `Message`: `{id: UUID, role: "user"|"assistant", content: string, timestamp: datetime, model: string|null, sources: list|null, agent_trace: dict|null, attachments: list|null, stats: MessageStats|null}`
- `MessageStats`: `{input_tokens: int, output_tokens: int, total_tokens: int, cost_usd: float, tokens_per_second: float, latency_ms: float}`
- `Folder`: `{_id: UUID, user_id: string, name: string, color: string, sort_order: int, created_at: datetime}`

**Storage:** MongoDB collection `chat_sessions`, `chat_folders` in the active database.

**Message Processing Flow:**
1. `POST /api/v1/sessions/{session_id}/messages` with `{content: string, agent_mode: string|null, strategy_id: string|null, attachments: list|null}`
2. Validates session ownership
3. Loads last 20 messages as conversation history
4. Configures `FederatedAgent` with mode, models, strategy
5. Calls `agent.process()` → returns `(response_text, AgentTrace)`
6. Calculates token costs using `MODEL_PRICING` lookup table
7. Appends user + assistant messages to session
8. Updates session stats (cumulative tokens, cost)
9. Returns response with sources, trace, and stats

**Streaming Endpoint:**
- `POST /api/v1/sessions/{session_id}/messages/stream`
- Returns `text/event-stream` (SSE)
- Event types: `start`, `phase`, `orchestrator_step`, `worker_step`, `response`, `error`, `done`
- Real-time callback `on_event` passed to `FederatedAgent.process()`
- Timeout: 10 minutes

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/sessions/` | List user's sessions |
| POST | `/api/v1/sessions/` | Create new session |
| GET | `/api/v1/sessions/{id}` | Get session with history |
| PUT | `/api/v1/sessions/{id}` | Update title/folder/model/pin |
| DELETE | `/api/v1/sessions/{id}` | Delete session |
| POST | `/api/v1/sessions/{id}/messages` | Send message (sync) |
| POST | `/api/v1/sessions/{id}/messages/stream` | Send message (SSE stream) |
| DELETE | `/api/v1/sessions/{id}/messages` | Clear messages |
| GET | `/api/v1/sessions/{id}/export` | Export as JSON |
| GET | `/api/v1/sessions/folders` | List folders |
| POST | `/api/v1/sessions/folders` | Create folder |
| PUT | `/api/v1/sessions/folders/{id}` | Update folder |
| DELETE | `/api/v1/sessions/folders/{id}` | Delete folder |
| GET | `/api/v1/sessions/archived/list` | List archived |
| POST | `/api/v1/sessions/archive` | Archive sessions |
| POST | `/api/v1/sessions/restore` | Restore archived |
| POST | `/api/v1/sessions/delete-permanent` | Permanent delete |
| GET | `/api/v1/sessions/meta/models` | Available models + pricing |
| GET | `/api/v1/sessions/meta/pricing` | Pricing table |
| POST | `/api/v1/sessions/meta/estimate-tokens` | Token estimation |

---

### 7. Document Ingestion Pipeline

**Purpose:** Convert documents from various formats into searchable, embedded chunks stored in MongoDB.

**Ingestion Job Model:**
```
{job_id: string, status: "pending"|"running"|"paused"|"completed"|"failed"|"cancelled"|"stopped", started_at: datetime, completed_at: datetime|null, total_files: int, processed_files: int, current_file: string, chunks_created: int, progress_percent: float, document_count: int, image_count: int, audio_count: int, video_count: int, failed_files: int, duplicates_skipped: int, error: string|null}
```

**Storage:** MongoDB collection `ingestion_jobs` with `_id = job_id`.

**Pipeline Phases:**
1. **Discovery:** Recursively scan configured `documents_folders`, build file list
2. **Processing:** Parse files using Docling (PDFs, DOCX, etc.), Whisper (audio: MP3, WAV), image OCR
3. **Chunking:** Split text into chunks with configurable `chunk_size` and `overlap`
4. **Embedding:** Generate vector embeddings using configured embedding provider in batches
5. **Storage:** Upsert documents and chunks into MongoDB with deduplication
6. **Classification:** Update file registry with processing result category

**Graceful Shutdown:**
- Signal handlers capture SIGTERM/SIGINT
- `_shutdown_requested` flag checked between file processing iterations
- Current job saved with `INTERRUPTED` status for resume on restart
- `check_and_resume_interrupted_jobs()` called on startup

**File Classification (FileClassification enum):**
- `NORMAL` — successfully processed with chunks
- `IMAGE_ONLY_PDF` — PDF with no extractable text (scanned without OCR)
- `NO_CHUNKS` — processed but produced 0 chunks
- `TIMEOUT` — processing exceeded timeout
- `ERROR` — processing error
- `PENDING` — never processed

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/ingestion/start` | Start ingestion job |
| GET | `/api/v1/ingestion/status` | Current job status |
| GET | `/api/v1/ingestion/status/{job_id}` | Specific job status |
| POST | `/api/v1/ingestion/stop` | Stop current job |
| POST | `/api/v1/ingestion/pause` | Pause current job |
| POST | `/api/v1/ingestion/resume` | Resume paused job |
| GET | `/api/v1/ingestion/history` | Job history list |
| GET | `/api/v1/ingestion/logs` | Ingestion log buffer |
| GET | `/api/v1/ingestion/stats` | Ingestion statistics |
| GET | `/api/v1/ingestion/documents` | List ingested documents |
| GET | `/api/v1/ingestion/documents/lookup` | Lookup specific document |
| DELETE | `/api/v1/ingestion/documents/{id}` | Delete document + chunks |

**Configuration (env vars):**
- `INGESTION_MAX_CONCURRENT_FILES` (default 2, range 1-10)
- `INGESTION_PROCESS_ISOLATION` (default true) — run in separate worker process
- Chunk size, overlap, batch sizes configurable in BackendSettings

---

### 8. Ingestion Queue & Scheduling

**Purpose:** Manage multiple ingestion jobs with prioritization, scheduling, and selective retry.

**Queue Models:**
- `QueuedIngestionJob`: `{id: UUID, profile_key: string, profile_name: string, documents_folder: string|null, file_types: list[string], incremental: bool, priority: int, status: "queued"|"running"|"completed"|"failed"|"cancelled", selective filters...}`
- `ScheduledIngestionJob`: `{id: UUID, profile_key: string, frequency: "hourly"|"daily"|"weekly"|"monthly", hour: int (0-23), day_of_week: int (0-6), day_of_month: int (1-31), enabled: bool, last_run: datetime|null, next_run: datetime|null, selective filters...}`

**File Type Filters:** `documents`, `images`, `audio`, `video`, `all`

**Selective Ingestion Filters:**
- `retry_image_only_pdfs: bool` — retry PDFs that had no extractable text
- `retry_timeouts: bool` — retry files that timed out
- `retry_errors: bool` — retry files that had processing errors
- `retry_no_chunks: bool` — retry files that produced zero chunks
- `skip_image_only_pdfs: bool` — skip known image-only PDFs

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/ingestion-queue/queue` | Add job to queue |
| GET | `/api/v1/ingestion-queue/queue` | List queued jobs |
| DELETE | `/api/v1/ingestion-queue/queue/{id}` | Cancel queued job |
| POST | `/api/v1/ingestion-queue/schedule` | Create scheduled job |
| GET | `/api/v1/ingestion-queue/schedules` | List scheduled jobs |
| PUT | `/api/v1/ingestion-queue/schedule/{id}` | Update schedule |
| DELETE | `/api/v1/ingestion-queue/schedule/{id}` | Delete schedule |
| POST | `/api/v1/ingestion-queue/schedule/{id}/toggle` | Enable/disable |

---

### 9. Embedding Provider System

**Purpose:** Abstract embedding generation across multiple providers with unified interface.

**Base Class:** `BaseEmbeddingProvider` (abstract)
- `generate_embeddings(texts: list[str], task_type: EmbeddingTaskType|null) -> Tuple[list[list[float]], metadata_dict]`
- `test_connection() -> dict`
- `get_dimension() -> int`
- `get_max_tokens() -> int`
- `estimate_cost(token_count) -> float`

**Providers:**
| Provider Class | Models | Dimensions | Task Types | Batch |
|---|---|---|---|---|
| `OpenAIEmbeddingProvider` | text-embedding-3-small (1536), text-embedding-3-large (3072), ada-002 (1536) | Fixed | No | Yes |
| `GeminiEmbeddingProvider` | gemini-embedding-2-preview (3072), gemini-embedding-001 (3072), text-embedding-004 (768) | Adjustable (256-3072) | Yes (RETRIEVAL_QUERY, RETRIEVAL_DOCUMENT, SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING, QUESTION_ANSWERING, FACT_VERIFICATION, CODE_RETRIEVAL_QUERY) | 100 per batch |
| `VoyageAIEmbeddingProvider` | voyage-4-large (1024), voyage-4 (1024), voyage-4-lite (1024), voyage-code-3 (1024), voyage-3-large (1024) | Adjustable (256-2048) | Yes (query, document) | 128 per batch |
| `OllamaEmbeddingProvider` | nomic-embed-text (768), mxbai-embed-large (1024), all-minilm (384), snowflake-arctic-embed (1024), bge-large (1024), bge-m3 (1024) | Fixed | No | Sequential |

**Configuration:**
- `EmbeddingProviderConfig`: `{provider: string, model: string, api_key: string, base_url: string|null, output_dimension: int|null, task_type: EmbeddingTaskType}`
- Provider selection via `EMBEDDING_PROVIDER` env var
- API key via `EMBEDDING_API_KEY` or provider-specific keys

**Metadata returned per call:**
```
{latency_ms: float, token_count: int, model: string, provider: string, dimension: int, task_type: string|null}
```

---

### 10. LLM Provider System

**Purpose:** Unified LLM interaction across providers using LiteLLM for routing.

**LLMProvider enum:** `openai`, `google`, `anthropic`, `ollama`, `openai_compatible`

**LLMConfig dataclass:**
```
{provider: LLMProvider, model: string, api_key: string|null, base_url: string|null, temperature: float (default 0.7), max_tokens: int (default 2000), extra_params: dict}
```

**Model String Format (LiteLLM):**
- OpenAI: `model_name` (no prefix)
- Google: `gemini/model_name`
- Anthropic: `anthropic/model_name`
- Ollama: `ollama/model_name`
- OpenAI-compatible: `openai/model_name` with custom `base_url`

**DualLLMConfig:** `{orchestrator: LLMConfig, worker: LLMConfig, embedding: LLMConfig|null}`

**LLMClient class:**
- `complete(messages, temperature, max_tokens, **kwargs) -> string`
- `complete_json(messages, ...) -> dict` — parses response as JSON, handles markdown code blocks
- Automatic parameter mapping: `max_tokens` → `max_completion_tokens` for compatible models

**LLMProviderManager (singleton):**
- `get_config() -> DualLLMConfig` — loads from DB or defaults
- `save_config(config) -> bool` — persists to `llm_config` collection
- `get_orchestrator_client() -> LLMClient`
- `get_worker_client() -> LLMClient`
- `invalidate_cache()` — force reload from DB

**Provider-specific API Key Resolution (`settings.get_api_key_for_provider(provider)`):**
- `openai` → `OPENAI_API_KEY` or `LLM_API_KEY`
- `google`/`gemini` → `GOOGLE_API_KEY` or `LLM_API_KEY`
- `anthropic`/`claude` → `ANTHROPIC_API_KEY` or `LLM_API_KEY`
- `voyageai`/`voyage` → `VOYAGE_API_KEY`
- `ollama` → empty (no key needed)

---

### 11. Agent Strategy System

**Purpose:** Customize agent behavior for different domains without changing core orchestration logic.

**BaseStrategy abstract class:**
- `metadata: StrategyMetadata` — `{id: string, name: string, description: string, version: string, domain: string, is_default: bool}`
- `get_analyze_prompt() -> string`
- `get_plan_prompt() -> string`
- `get_evaluate_prompt() -> string`
- `get_synthesize_prompt() -> string`
- `process_analysis(analysis_result: dict) -> dict` — post-process analysis output
- `get_custom_rrf_weights() -> dict|null` — custom RRF scoring weights
- `get_search_config() -> dict` — search parameter overrides

**Built-in Strategies:**
| ID | Name | Domain | Description |
|---|---|---|---|
| `legacy` | Legacy Strategy | general | Original behavior, baseline compatibility |
| `enhanced` | Enhanced Strategy | general | Query classification, entity extraction, source prioritization (default) |
| `software_dev` | Software Development | software | Code-aware search, technical documentation focus |
| `legal` | Legal Analysis | legal | Legal document analysis, compliance focus |
| `hr` | HR Processes | hr | Employee data, policy lookup |

**StrategyRegistry (static):**
- `register(strategy: BaseStrategy)` — register strategy instance
- `get(strategy_id: string) -> BaseStrategy`
- `get_default() -> BaseStrategy`
- `list_all() -> list[StrategyMetadata]`

**Strategy Resolution Priority (in FederatedAgent):**
1. Explicit `strategy` instance parameter
2. Explicit `strategy_id` parameter
3. `config.strategy_override` (direct ID)
4. `config.strategy` (StrategySelection enum mapping)
5. Default strategy from registry

**Strategy Metrics (strategies/metrics.py):**
- Tracks per-strategy performance: response time, token usage, result quality
- Used for A/B testing and strategy comparison

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/strategies/` | List available strategies |
| GET | `/api/v1/strategies/{id}` | Get strategy details |
| GET | `/api/v1/strategies/active` | Get currently active strategy |
| POST | `/api/v1/strategies/select` | Select active strategy |
| GET | `/api/v1/strategies/metrics` | Get strategy performance metrics |

---

### 12. Cloud Source Integration

**Purpose:** Connect, authenticate, and sync files from external cloud storage providers.

**Provider Base Class (providers/base.py — `BaseCloudProvider`):**
- `authenticate(credentials: dict) -> bool`
- `list_files(folder_id: string|null, page_token: string|null) -> Tuple[list[RemoteFile], string|null]`
- `list_folders(parent_id: string|null) -> list[RemoteFolder]`
- `download_file(file_id: string, destination: Path) -> bool`
- `get_delta(delta_token: string|null) -> SyncDelta`
- `get_capabilities() -> ProviderCapabilities`
- `refresh_token(refresh_token: string) -> OAuthTokens`

**Supported Providers:**
| Type | Auth | Delta Sync | Implementation |
|---|---|---|---|
| Google Drive | OAuth2 | Yes | `google_drive.py` |
| Dropbox | OAuth2 | Yes | `dropbox_provider.py` |
| WebDAV (OwnCloud, NextCloud) | Password | No | `webdav.py` |
| OneDrive | OAuth2 | Yes | Planned |

**Data Models:**
- `RemoteFile`: `{id, name, path, mime_type, size_bytes, modified_at, created_at, checksum, download_url, parent_id, web_view_url, thumbnail_url, version_id, etag, provider_metadata}`
- `RemoteFolder`: `{id, name, path, parent_id, children_count, modified_at, has_children, is_root}`
- `SyncDelta`: `{added: list[RemoteFile], modified: list[RemoteFile], deleted: list[file_ids], next_delta_token, has_more, total_changes}`
- `OAuthTokens`: `{access_token, refresh_token, expires_at, token_type, scope}`
- `ProviderCapabilities`: `{provider_type, display_name, description, icon, supported_auth_types, oauth_scopes, supports_delta_sync, supports_webhooks, supports_file_streaming, supports_folders, supports_files, supports_attachments, rate_limit_requests_per_minute, rate_limit_bytes_per_day}`

**Cloud File Cache (file_cache.py):**
- LFU (Least Frequently Used) eviction policy
- Persistent metadata tracking (access count, last access time)
- Configurable max cache size

**OAuth Flow:**
1. Client requests auth URL via `GET /api/v1/cloud-sources/oauth/{provider}/auth-url`
2. User completes OAuth in browser
3. Callback received at `GET /api/v1/cloud-sources/oauth/{provider}/callback`
4. Tokens encrypted via CredentialVault and stored in `cloud_source_connections` collection
5. TokenManager handles refresh before expiration (300s buffer)

**API Endpoints (prefix `/api/v1/cloud-sources`):**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/providers` | List available providers + capabilities |
| GET | `/providers/{type}` | Get provider details |
| POST | `/connections` | Create connection |
| GET | `/connections` | List connections for profile |
| GET | `/connections/{id}` | Get connection details |
| PUT | `/connections/{id}` | Update connection |
| DELETE | `/connections/{id}` | Delete connection |
| POST | `/connections/{id}/test` | Test connection |
| GET | `/oauth/{provider}/auth-url` | Get OAuth authorization URL |
| GET | `/oauth/{provider}/callback` | OAuth callback handler |
| POST | `/sync/{connection_id}/start` | Start sync |
| GET | `/sync/{connection_id}/status` | Get sync status |
| POST | `/sync/{connection_id}/stop` | Stop sync |
| GET | `/sync/history` | Sync history |
| GET | `/cache/stats` | Cache statistics |
| POST | `/cache/clear` | Clear cache |
| GET | `/cache/files` | List cached files |

---

### 13. Email Integration

**Purpose:** Sync emails from various email providers into the RAG knowledge base.

**Providers (providers/email/):**
- `GmailProvider` — Gmail API with OAuth2, label-based filtering
- `OutlookProvider` — Microsoft Graph API with OAuth2, folder-based filtering
- `IMAPProvider` — Generic IMAP with username/password, folder selection

**Capabilities:**
- Incremental sync via message IDs / delta tokens
- Attachment extraction and processing
- HTML to text conversion
- Configurable date range filtering
- Folder/label include/exclude filters

---

### 14. Airbyte Connector Integration

**Purpose:** Integrate structured data sources (Confluence, Jira) via Airbyte connectors.

**Components (providers/airbyte/):**
- Airbyte API client for source/connection/sync management
- Confluence connector: page/space sync with rich text extraction
- Jira connector: issue/comment sync with metadata
- Email connector: email sync via Airbyte

**AirbyteConfig (per profile):**
```
{workspace_id: string, workspace_name: string, destination_id: string, default_sync_mode: "incremental"|"full_refresh", default_schedule_type: "manual"|"cron", default_schedule_cron: string|null}
```

---

### 15. Prompt Template Management

**Purpose:** Customizable, versioned system prompts with tool definitions for agent behavior tuning.

**Data Model:**
- `PromptTemplate`: `{id: string, name: string, description: string, category: "chat"|"search"|etc, versions: list[PromptVersion], active_version: int, created_at, updated_at, created_by}`
- `PromptVersion`: `{version: int, system_prompt: string, tools: list[ToolSchema], created_at, created_by, notes: string, is_active: bool}`
- `ToolSchema`: `{name: string, description: string, parameters: list[ToolParameterSchema], enabled: bool}`

**Storage:** MongoDB collection `prompt_templates`.

**Default Templates:** Initialized on startup via `initialize_default_templates()` for agent phases (analyze, plan, evaluate, synthesize).

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/prompts/` | List all templates |
| POST | `/api/v1/prompts/` | Create template |
| GET | `/api/v1/prompts/{id}` | Get template with versions |
| PUT | `/api/v1/prompts/{id}` | Update metadata |
| DELETE | `/api/v1/prompts/{id}` | Delete template |
| POST | `/api/v1/prompts/{id}/versions` | Create new version |
| PUT | `/api/v1/prompts/{id}/versions/{ver}/activate` | Activate version |
| POST | `/api/v1/prompts/test` | Test prompt with mock tools |

---

### 16. Model Version Registry

**Purpose:** Catalog of all supported models with metadata for UI selection and parameter adaptation.

**Registry Data (model_versions.py):**
- Per-model: `{provider, model_id, display_name, description, pricing: {input, output per 1M tokens}, capabilities: list, context_window, max_output_tokens, parameter_mapping: dict}`
- Parameter mapping: maps generic params to provider-specific (e.g., `max_tokens` → `max_completion_tokens` for O1 models)

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/model-versions/` | List all available models |
| GET | `/api/v1/model-versions/{id}` | Get model details |
| GET | `/api/v1/model-versions/providers` | List providers |
| GET | `/api/v1/model-versions/recommended` | Get recommended models |

---

### 17. Credential Vault

**Purpose:** Encrypt sensitive data at rest using Fernet symmetric encryption.

**CredentialVault class:**
- Constructor: `CredentialVault(master_key: string|null)` — reads from env `CREDENTIAL_VAULT_KEY` if not provided
- Key derivation: `PBKDF2HMAC(SHA256, 32 bytes, 100000 iterations)` from master key + salt
- Salt from env `CREDENTIAL_VAULT_SALT` (default in dev mode)

**Methods:**
- `encrypt(data: dict) -> string` — JSON serialize → Fernet encrypt → base64 encode
- `decrypt(encrypted_data: string) -> dict` — reverse of encrypt
- `encrypt_field(value: string) -> string` — encrypt single string
- `decrypt_field(encrypted_data: string) -> string` — decrypt single string
- `rotate_key(new_key: string, encrypted_credentials: list[string]) -> list[string]` — re-encrypt all with new key

**TokenManager class:**
- `is_token_expired(expires_at, buffer_seconds=300) -> bool`
- Token refresh handling for OAuth providers

**Singleton:** `get_vault() -> CredentialVault`

---

### 18. Backup & Restore System

**Purpose:** Data protection with full and incremental backup capabilities.

**BackupService class (services/backup_service.py):**
- `create_backup(profile_key, backup_type="full") -> BackupMetadata`
- `create_checkpoint(profile_key) -> BackupMetadata`
- `restore_backup(backup_id, mode="full") -> RestoreResult`
- `list_backups(profile_key) -> list[BackupMetadata]`
- `get_backup_chain(backup_id) -> list[BackupMetadata]`
- `delete_backup(backup_id) -> bool`
- `get_storage_stats() -> dict`

**Collections backed up:** `documents`, `chunks`, `ingestion_jobs`, `ingestion_stats`, `failed_documents`, `users`, `chat_sessions`, `prompt_templates`, `file_registry`, `llm_config`

**Restore Modes:**
- `FULL` — drop existing collections, restore from backup
- `MERGE` — combine backup data with existing (skip duplicates)
- `INCREMENTAL` — apply only delta changes

**Retention Policy:**
- Default: 30 days, max 10 backups per profile
- Configurable via `PUT /api/v1/backups/config`
- Auto-cleanup of expired backups

**Post-ingestion auto-backup:** Triggered via `trigger_post_ingestion_backup()` after successful ingestion completion.

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/backups/create` | Create full backup |
| POST | `/api/v1/backups/checkpoint` | Create checkpoint |
| GET | `/api/v1/backups/` | List backups |
| GET | `/api/v1/backups/checkpoints` | List checkpoints |
| GET | `/api/v1/backups/config` | Get config |
| PUT | `/api/v1/backups/config` | Update config |
| GET | `/api/v1/backups/status` | Current status |
| GET | `/api/v1/backups/storage` | Storage stats |
| GET | `/api/v1/backups/{id}` | Backup details |
| GET | `/api/v1/backups/{id}/chain` | Backup chain |
| POST | `/api/v1/backups/{id}/restore` | Restore |
| DELETE | `/api/v1/backups/{id}` | Delete |

---

### 19. File Registry & Classification

**Purpose:** Track every file's processing status, enable selective retry and change detection.

**FileRegistryEntry model:**
```
{id: string, file_path: string, file_name: string, file_size_bytes: int, content_hash: SHA256, file_modified_at: datetime, classification: FileClassification, last_processed_at: datetime|null, last_job_id: string|null, chunks_created: int, processing_time_ms: float, error_message: string|null, retry_count: int, profile_key: string, created_at: datetime, updated_at: datetime}
```

**FileRegistryStats:** `{total_files, normal, image_only_pdf, no_chunks, timeout, error, pending, modified_since_last_run, total_size_bytes}`

**FileRegistryService (services/file_registry.py):**
- `register_file(file_path, profile_key) -> FileRegistryEntry`
- `update_classification(file_path, classification, job_id, chunks_created, processing_time_ms, error_message)`
- `get_files_by_classification(profile_key, classification) -> list`
- `get_modified_files(profile_key) -> list` — files where `file_modified_at > last_processed_at`
- `get_stats(profile_key) -> FileRegistryStats`

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/file-registry/` | List files (paginated, filterable) |
| GET | `/api/v1/file-registry/stats` | Registry statistics |
| GET | `/api/v1/file-registry/{id}` | File details |
| PUT | `/api/v1/file-registry/{id}/classification` | Update classification |
| POST | `/api/v1/file-registry/scan` | Scan for new/modified files |

---

### 20. Search Index Management

**Purpose:** Create and monitor MongoDB Atlas Vector Search and Text Search indexes.

**Index Types:**
- **Vector Search Index:** On `chunks` collection, field `embedding`, similarity `cosine`, dimensions from config
- **Text Search Index:** On `chunks` collection, field `content`, with fuzzy matching support

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/indexes/` | List all indexes with status |
| POST | `/api/v1/indexes/vector` | Create vector search index |
| POST | `/api/v1/indexes/text` | Create text search index |
| DELETE | `/api/v1/indexes/{name}` | Delete index |
| GET | `/api/v1/indexes/status` | Index health status |

---

### 21. System Administration

**Purpose:** System monitoring, configuration, and diagnostics.

**System Router Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/system/health` | Health check (DB connectivity) |
| GET | `/api/v1/system/stats` | Database and system statistics |
| GET | `/api/v1/system/config` | Current config (non-sensitive) |
| PUT | `/api/v1/system/config` | Update system configuration |
| GET | `/api/v1/system/version` | API version info |

**Admin Router Endpoints (prefix `/api/v1/admin`):**
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/dashboard` | Admin | System health dashboard |
| GET | `/health/detailed` | Admin | Detailed health info |
| GET | `/config` | Admin | Full system configuration |
| POST | `/config` | Admin | Update configuration |
| GET | `/logs` | Admin | System logs |
| GET | `/logs/stream` | Admin | Stream logs (SSE) |

**Status Router Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/status/` | Dashboard overview |
| GET | `/api/v1/status/ingestion` | Ingestion status |
| GET | `/api/v1/status/search` | Search health |

---

### 22. Security Middleware

**Purpose:** Protect the API with rate limiting, security headers, and request timeouts.

**RateLimitMiddleware:**
- Sliding window algorithm per client IP + endpoint category
- Rate limits:
  - `auth_login`: 5/min, `auth_register`: 3/5min, `auth_password`: 3/5min
  - `api_search`: 30/min, `api_chat`: 20/min, `api_ingestion`: 60/min
  - `api_ingestion_read`: 200/min, `api_general`: 100/min
- Brute force detection: 10+ auth failures in 10 min → 5 min lockout
- Returns 429 with `Retry-After` header

**SecurityHeadersMiddleware:**
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Content-Security-Policy` with restrictive defaults
- `Strict-Transport-Security` in production

**RequestTimeoutMiddleware:**
- Health checks: 5s
- Standard requests: 30s
- Chat/agent endpoints: 300s (5 min)
- Auto-converts to background on timeout

---

### 23. Browser Tool

**Purpose:** Fetch and extract content from web pages for real-time information in conversations.

**browse_url function (tools/browser_tool.py):**
- Input: URL string
- Fetches page with httpx
- Extracts text content (strips HTML tags)
- Returns `BrowserToolResult`: `{url, title, content, success, error}`
- Used as LLM tool in chat: `browse_web` function in TOOLS_SCHEMA
- Timeout: 30 seconds

**LLM Tool Definition:**
```json
{"name": "browse_web", "description": "Fetch and read content from a web page URL", "parameters": {"url": {"type": "string", "description": "URL to fetch"}}}
```

---

### 24. Embedding Benchmark System

**Purpose:** Compare embedding providers on latency, quality, and cost.

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/benchmark/run` | Run benchmark with test texts |
| POST | `/api/v1/benchmark/run-file` | Benchmark with file input |
| GET | `/api/v1/benchmark/providers` | List available providers |
| POST | `/api/v1/benchmark/test-provider` | Test single provider |
| GET | `/api/v1/benchmark/results` | List past results |
| GET | `/api/v1/benchmark/results/{id}` | Get specific result |
| DELETE | `/api/v1/benchmark/results/{id}` | Delete result |

---

### 25. Local LLM Management

**Purpose:** Manage Ollama models for fully offline/local LLM operation.

**API Endpoints:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/local-llm/models` | List installed Ollama models |
| POST | `/api/v1/local-llm/pull` | Pull/download model |
| DELETE | `/api/v1/local-llm/models/{name}` | Delete model |
| POST | `/api/v1/local-llm/test` | Test model with prompt |
| GET | `/api/v1/local-llm/status` | Ollama server status |

---

### 26. Update Service

**Purpose:** Manage offline software updates with integrity verification and rollback.

**UpdateService class (services/update_service.py):**
- Scans for `.rhu` update packages
- Package verification: SHA256 checksum + Ed25519 signature
- Pre-update backup before applying
- Container image loading via Docker
- Database migration execution
- Custom install scripts
- Rollback from pre-update backup
- Update history tracking

---

### 27. Frontend Application

**Purpose:** Web UI for all system interactions.

**Tech Stack:** React 18, TypeScript, Vite, Material-UI, Tailwind CSS, Axios, React Router

**Directory Structure:**
- `src/pages/` — 35+ page components (Chat, Search, Settings, Admin, Profiles, Ingestion, CloudSources, etc.)
- `src/components/` — 22+ reusable components (Sidebar, ChatMessage, SearchResults, FileUpload, etc.)
- `src/contexts/` — 9 context providers (AuthContext, ProfileContext, ThemeContext, SettingsContext, etc.)
- `src/hooks/` — 5 custom hooks (useAuth, useApi, useChat, useSearch, useProfile)
- `src/api/` — API client with Axios interceptors for JWT token injection
- `src/i18n/` — Internationalization support

**Deployment:** Served via nginx on port 11080, proxies API requests to backend port 11000.

---

### 28. Telemetry System

**Purpose:** Full interaction trace capture for debugging, compliance, and analytics.

**Capabilities:**
- LLM calls, search operations, and tool executions recorded per request
- Dual-mode PII protection: `protected` (pseudonymized) + `raw` (for trusted partners)
- Triple-engine pseudonymizer: regex patterns + spaCy NER + Presidio analyzer
- Admin API for runtime configuration (enable/disable, set PII mode, retention)
- JSONL storage with configurable retention policies

**Integration:** Standalone Electron viewer app at `tools/telemetry-viewer/`

---

## Appendix A: Database Collections Reference

| Collection | Database | Purpose |
|---|---|---|
| `documents` | Profile DB | Document metadata (title, source, timestamps) |
| `chunks` | Profile DB | Text chunks with embedding vectors |
| `users` | Active DB | User accounts |
| `api_keys` | Active DB | API key hashes and metadata |
| `profile_access` | Active DB | User-profile permissions |
| `chat_sessions` | Active DB | Conversation history |
| `chat_folders` | Active DB | Session folder organization |
| `ingestion_jobs` | Active DB | Ingestion job history |
| `file_registry` | Active DB | File tracking and classification |
| `prompt_templates` | Active DB | Customizable prompts |
| `llm_config` | Active DB | Persisted LLM configuration |
| `cloud_source_connections` | Active DB | OAuth credentials (encrypted) |
| `cloud_source_syncs` | Active DB | Sync status and delta tokens |
| `backup_config` | Active DB | Backup settings |
| `backups_metadata` | Active DB | Backup records |

## Appendix B: Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017/?directConnection=true` | MongoDB connection string |
| `MONGODB_DATABASE` | `rag_db` | Default database name |
| `LLM_PROVIDER` | `openai` | Primary LLM provider |
| `LLM_API_KEY` | — | Primary LLM API key |
| `LLM_MODEL` | `gpt-4o` | Primary LLM model |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Primary LLM base URL |
| `OPENAI_API_KEY` | — | OpenAI-specific key |
| `GOOGLE_API_KEY` | — | Google Gemini-specific key |
| `ANTHROPIC_API_KEY` | — | Anthropic Claude-specific key |
| `VOYAGE_API_KEY` | — | Voyage AI-specific key |
| `EMBEDDING_PROVIDER` | `openai` | Embedding provider |
| `EMBEDDING_API_KEY` | — | Embedding API key |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model |
| `EMBEDDING_DIMENSION` | `1536` | Embedding vector dimensions |
| `EMBEDDING_BASE_URL` | `https://api.openai.com/v1` | Embedding API base URL |
| `ORCHESTRATOR_MODEL` | `gpt-4o` | Orchestrator (thinking) model |
| `ORCHESTRATOR_PROVIDER` | `openai` | Orchestrator provider |
| `WORKER_MODEL` | `gemini-2.0-flash-exp` | Worker (fast) model |
| `WORKER_PROVIDER` | `google` | Worker provider |
| `AGENT_MAX_ITERATIONS` | `3` | Max orchestrator-worker loops |
| `AGENT_PARALLEL_WORKERS` | `4` | Max concurrent workers |
| `AGENT_WORKER_TIMEOUT` | `60` | Worker task timeout (seconds) |
| `AGENT_ORCHESTRATOR_TIMEOUT` | `120` | Orchestrator phase timeout |
| `AGENT_TOTAL_TIMEOUT` | `300` | Total agent request timeout |
| `JWT_SECRET_KEY` | `recallhub-secret-key-...` | JWT signing key (32+ chars in prod) |
| `REGISTRATION_MODE` | `open` | Registration: open/invite/closed |
| `INVITE_CODE` | — | Single invite code |
| `INVITE_CODES` | — | Comma-separated invite codes |
| `CREDENTIAL_VAULT_KEY` | — | Master encryption key |
| `CREDENTIAL_VAULT_SALT` | — | Encryption salt |
| `BRAVE_SEARCH_API_KEY` | — | Brave Search API key for web search |
| `PROFILES_PATH` | `profiles.yaml` | Path to profiles config |
| `APP_ENV` | `development` | Environment (development/production) |
| `EXPOSE_API_DOCS` | `true` (dev) | Show Swagger/ReDoc |
| `CORS_ORIGINS` | `localhost:3000,...` | Allowed CORS origins |
| `AIRBYTE_ENABLED` | `false` | Enable Airbyte integration |
| `AIRBYTE_API_URL` | `http://airbyte-server:8001` | Airbyte API URL |
| `INGESTION_MAX_CONCURRENT_FILES` | `2` | Max parallel file processing |
| `INGESTION_PROCESS_ISOLATION` | `true` | Separate worker process |

## Appendix C: Docker Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| `mongodb` | `mongodb/mongodb-atlas-local:8.0` | 11017 | Database with vector search |
| `backend` | Custom (Dockerfile.fast) | 11000 | FastAPI REST API |
| `ingestion-worker` | Custom (Dockerfile.worker) | — | Background document processing |
| `frontend` | Custom (React + nginx) | 11080 | Web UI |
| `cli` | Custom (optional) | — | Terminal access |

## Appendix D: API Route Map

**Base URL:** `http://localhost:11000`

| Prefix | Tag | Router Module |
|---|---|---|
| `/api/v1/auth` | Authentication | `routers/auth.py` |
| `/api/v1/chat` | Chat | `routers/chat.py` |
| `/api/v1/search` | Search | `routers/search.py` |
| `/api/v1/sessions` | Chat Sessions | `routers/sessions.py` |
| `/api/v1/profiles` | Profiles | `routers/profiles.py` |
| `/api/v1/ingestion` | Ingestion | `routers/ingestion.py` |
| `/api/v1/ingestion-queue` | Ingestion Queue | `routers/ingestion_queue.py` |
| `/api/v1/file-registry` | File Registry | `routers/file_registry.py` |
| `/api/v1/system` | System | `routers/system.py` |
| `/api/v1/status` | Status Dashboard | `routers/status.py` |
| `/api/v1/indexes` | Search Indexes | `routers/indexes.py` |
| `/api/v1/prompts` | Prompt Management | `routers/prompts.py` |
| `/api/v1/model-versions` | Model Versions | `routers/model_versions.py` |
| `/api/v1/strategies` | Agent Strategies | `routers/strategies.py` |
| `/api/v1/backups` | Backup & Restore | `routers/backup.py` |
| `/api/v1/benchmark` | Embedding Benchmark | `routers/embedding_benchmark.py` |
| `/api/v1/local-llm` | Local LLM | `routers/local_llm.py` |
| `/api/v1/cloud-sources` | Cloud Sources | `routers/cloud_sources/` |
| `/api/v1/admin` | Admin | `routers/admin.py` |
