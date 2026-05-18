# RecallHub - System Blueprints

## Table of Contents
1. [Backend Core Services](#1-backend-core-services)
2. [Federated Agent Architecture](#2-federated-agent-architecture)
3. [Ingestion Queue System](#3-ingestion-queue-system)
4. [Workers Architecture](#4-workers-architecture)
5. [Frontend Architecture](#5-frontend-architecture)
6. [i18n System](#6-i18n-system)
7. [Docker Deployment Strategy](#7-docker-deployment-strategy)
8. [Additional Database Collections](#8-additional-database-collections)
9. [Legacy Agent Strategies System](#9-legacy-agent-strategies-system)
10. [File Registry Service](#10-file-registry-service)
11. [Backup Service](#11-backup-service)
12. [Embedding Benchmark Service](#12-embedding-benchmark-service)

> **Strategy OS:** The config-driven DAG runtime, evaluation, and overnight exploration scheduler are documented in dedicated blueprints [07-STRATEGY_OS_OVERVIEW.md](./07-STRATEGY_OS_OVERVIEW.md) → [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md). This file covers backend service infrastructure that Strategy OS builds on.

---

## 1. Backend Core Services

### 1.1 Configuration Service (`backend/core/config.py`)

**Purpose:** Centralized runtime configuration management with database persistence.

**Key Features:**
- Load configuration from environment variables with defaults
- Persist configuration changes to MongoDB `config` collection
- Support for ingestion performance tuning parameters
- Thread-safe configuration updates

**Configuration Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `concurrent_files` | int | 4 | Files processed in parallel |
| `embedding_batch_size` | int | 100 | Chunks per embedding batch |
| `max_file_size_mb` | int | 100 | Maximum file size to process |
| `chunk_size` | int | 1000 | Target chunk size (characters) |
| `chunk_overlap` | int | 200 | Overlap between chunks |
| `enable_ocr` | bool | True | Enable OCR for images |
| `enable_audio_transcription` | bool | True | Enable Whisper transcription |

**Usage Pattern:**
```python
from backend.core.config import get_config, update_config

# Read configuration
config = get_config()
batch_size = config.embedding_batch_size

# Update configuration (persists to DB)
await update_config({"embedding_batch_size": 50})
```

---

### 1.2 Database Manager (`backend/core/database.py`)

**Purpose:** Manages MongoDB connections with async/sync client access and profile-aware database switching.

**Architecture:**
```
DatabaseManager
├── async_client (pymongo.AsyncMongoClient)
├── sync_client (MongoClient)
├── current_database_name
├── documents_collection
├── chunks_collection
└── switch_profile(profile_key)
```

**Key Features:**
- Connection pooling with configurable pool size
- Automatic reconnection on failure
- Profile-based database isolation
- Collection references for documents/chunks

**Profile Switching:**
```python
# Switch to different profile database
await db.switch_profile("parhelion")
# Now db.documents_collection points to rag_parhelion.documents
```

---

### 1.3 Credential Vault (`backend/core/credential_vault.py`)

**Purpose:** Secure storage and retrieval of OAuth tokens and API credentials.

**Encryption:**
- AES-256 encryption at rest
- Master key derived from `CREDENTIAL_VAULT_KEY` environment variable
- Per-credential encryption with unique IVs

**Schema:**
```python
class EncryptedCredential:
    connection_id: str      # Reference to cloud connection
    provider_type: str      # google_drive, dropbox, etc.
    encrypted_data: bytes   # AES-256 encrypted JSON
    iv: bytes              # Initialization vector
    created_at: datetime
    expires_at: Optional[datetime]
```

**Operations:**
```python
vault = CredentialVault()

# Store credentials
await vault.store(connection_id, {
    "access_token": "...",
    "refresh_token": "...",
    "expires_at": "2026-01-01T00:00:00Z"
})

# Retrieve and decrypt
creds = await vault.retrieve(connection_id)

# Delete on disconnect
await vault.delete(connection_id)
```

---

### 1.4 File Cache (`backend/core/file_cache.py`)

**Purpose:** Local file caching for cloud-sourced documents during processing.

**Features:**
- LRU eviction policy
- Configurable cache size limit
- Async file operations
- Checksum validation

**Cache Structure:**
```
/tmp/recallhub_cache/
├── {connection_id}/
│   ├── {file_hash}.pdf
│   ├── {file_hash}.docx
│   └── ...
└── metadata.json
```

**Configuration:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `CACHE_DIR` | `/tmp/recallhub_cache` | Cache directory |
| `MAX_CACHE_SIZE_GB` | 10 | Maximum cache size |
| `CACHE_TTL_HOURS` | 24 | File TTL before eviction |

---

### 1.5 LLM Providers (`backend/core/llm_providers.py`)

**Purpose:** Unified interface for multiple LLM providers with dynamic model switching.

**Supported Providers:**

| Provider | Models | Features |
|----------|--------|----------|
| OpenAI | gpt-4o, gpt-4o-mini, gpt-3.5-turbo | Streaming, function calling |
| Google | gemini-2.0-flash, gemini-1.5-pro | Streaming, vision |
| Anthropic | claude-3-sonnet, claude-3-haiku | Streaming, long context |
| Ollama | llama3, qwen2.5, mistral | Local, offline mode |
| OpenRouter | Any model via routing | Cost optimization |

**Configuration Model:**
```python
class LLMConfig:
    orchestrator_provider: str  # Provider for thinking
    orchestrator_model: str     # Model for orchestrator
    worker_provider: str        # Provider for workers
    worker_model: str           # Model for workers
    embedding_provider: str     # Provider for embeddings
    embedding_model: str        # Embedding model name
```

**Provider Selection Logic:**
```python
def get_llm_client(role: str) -> LLMClient:
    config = get_llm_config()
    if role == "orchestrator":
        return create_client(config.orchestrator_provider, config.orchestrator_model)
    elif role == "worker":
        return create_client(config.worker_provider, config.worker_model)
```

---

### 1.6 Model Versions (`backend/core/model_versions.py`)

**Purpose:** Track available model versions and fetch latest from providers.

**Schema:**
```python
class ModelVersion:
    provider: str           # openai, google, anthropic
    model_id: str          # gpt-4o, gemini-2.0-flash
    display_name: str      # GPT-4o
    context_window: int    # 128000
    input_price: float     # per 1M tokens
    output_price: float    # per 1M tokens
    supports_vision: bool
    supports_streaming: bool
    supports_function_calling: bool
    last_updated: datetime
```

**Auto-Discovery:**
- Fetches model lists from provider APIs on startup
- Caches in `model_versions` collection
- Refreshes every 24 hours

---

### 1.7 Profile Models (`backend/core/profile_models.py`)

**Purpose:** Pydantic models for profile configuration and validation.

**Models:**
```python
class Profile(BaseModel):
    key: str                    # Unique identifier
    name: str                   # Display name
    description: Optional[str]
    documents_folders: List[str]  # Local paths
    database: str               # MongoDB database name
    collection_documents: str = "documents"
    collection_chunks: str = "chunks"
    vector_index: str = "vector_index"
    text_index: str = "text_index"
    embedding_model: Optional[str]
    llm_model: Optional[str]
    cloud_sources: List[CloudSourceConfig] = []
    airbyte: Optional[AirbyteConfig]

class CloudSourceConfig(BaseModel):
    connection_id: str
    provider_type: str
    display_name: str
    enabled: bool = True
    sync_schedule: Optional[str]  # Cron expression
    include_paths: List[str] = []
    exclude_paths: List[str] = []
    collection_prefix: str = ""
```

---

### 1.8 Security (`backend/core/security.py`)

**Purpose:** Authentication and authorization utilities.

**JWT Authentication:**
```python
# Token generation
token = create_access_token(
    data={"sub": user.id, "email": user.email},
    expires_delta=timedelta(days=7)
)

# Token validation
payload = verify_token(token)
user_id = payload["sub"]
```

**API Key Authentication:**
```python
# Key generation
key, key_hash = generate_api_key()
# Returns: ("rh_abc123...", "sha256:...")

# Key verification
is_valid = verify_api_key(provided_key, stored_hash)
```

**Rate Limiting:**
| Endpoint Type | Rate Limit |
|--------------|------------|
| Auth endpoints | 10/minute |
| Search endpoints | 60/minute |
| Chat endpoints | 30/minute |
| Ingestion endpoints | 5/minute |
| Admin endpoints | 30/minute |

---

## 2. Federated Agent Architecture

### 2.1 Overview

The Federated Agent uses an Orchestrator-Worker pattern for complex query handling across multiple data sources.

```
User Query
    │
    ▼
┌─────────────────────────────────────┐
│           Coordinator               │
│  - Route to appropriate handler     │
│  - Manage streaming responses       │
│  - Aggregate worker results         │
└─────────────────────────────────────┘
    │
    ├─── Simple Query ──▶ Direct Response
    │
    └─── Complex Query ──▶ Orchestrator
                              │
                         ┌────┴────┐
                         │         │
                    ┌────▼────┐ ┌──▼───┐
                    │Orchestrator│Worker│
                    │  (Plan)   ││Pool │
                    └────┬────┘ └──┬───┘
                         │         │
                    Task Plan  Execute Tasks
                         │         │
                         └────┬────┘
                              │
                         Synthesize
                              │
                              ▼
                       Final Response
```

---

### 2.2 Coordinator (`backend/agent/coordinator.py`)

**Purpose:** Entry point for all agent requests, routes to appropriate handler.

**Responsibilities:**
1. Receive user query and context
2. Determine query complexity
3. Route to direct response or orchestrator
4. Manage streaming response delivery
5. Aggregate results from workers

**Query Classification:**
```python
class QueryComplexity(Enum):
    SIMPLE = "simple"        # Greeting, clarification
    SEARCH = "search"        # Single-source search
    FEDERATED = "federated"  # Multi-source search
    COMPLEX = "complex"      # Requires planning/reasoning
```

**Streaming Protocol:**
```python
async def stream_response(query: str) -> AsyncIterator[AgentEvent]:
    yield AgentEvent(type="start", data={"query": query})
    yield AgentEvent(type="thinking", data={"phase": "analyzing"})
    yield AgentEvent(type="search", data={"source": "profile_db"})
    yield AgentEvent(type="token", data={"text": "Based on..."})
    yield AgentEvent(type="complete", data={"stats": {...}})
```

---

### 2.3 Orchestrator (`backend/agent/orchestrator.py`)

**Purpose:** Plans and coordinates complex multi-step queries.

**Phases:**

| Phase | Description | Output |
|-------|-------------|--------|
| ANALYZE | Parse intent, extract entities | QueryAnalysis |
| PLAN | Create execution plan | TaskPlan |
| EXECUTE | Dispatch tasks to workers | TaskResults |
| EVALUATE | Assess result quality | EvaluationResult |
| SYNTHESIZE | Generate final response | SynthesizedResponse |

**Orchestrator Model:**
- Uses higher-capability model (e.g., GPT-4o)
- Has planning and reasoning capabilities
- Does NOT execute searches directly

**Task Planning:**
```python
class TaskPlan(BaseModel):
    tasks: List[Task]
    execution_order: List[List[str]]  # Parallel groups
    estimated_duration_ms: int
    reasoning: str

class Task(BaseModel):
    id: str
    type: TaskType  # search_profile, search_all, web_search, etc.
    description: str
    parameters: Dict[str, Any]
    dependencies: List[str] = []
```

---

### 2.4 Worker Pool (`backend/agent/worker_pool.py`)

**Purpose:** Execute tasks in parallel using fast, cost-effective models.

**Worker Configuration:**
```python
class WorkerPoolConfig:
    max_workers: int = 4          # Parallel worker limit
    worker_model: str             # Fast model (e.g., Gemini Flash)
    task_timeout_seconds: int = 30
    retry_attempts: int = 2
```

**Task Types:**

| Type | Description | Worker Action |
|------|-------------|---------------|
| `search_profile` | Search current profile | Vector + text search |
| `search_all` | Search all profiles | Federated search |
| `web_search` | Internet search | Brave/Google API |
| `browse_url` | Fetch URL content | HTTP fetch + extract |
| `email_search` | Search emails | Connected email sources |
| `confluence_search` | Search Confluence | Confluence API |

**Execution Flow:**
```python
async def execute_tasks(tasks: List[Task]) -> List[TaskResult]:
    # Group by dependencies
    execution_groups = topological_sort(tasks)
    
    results = []
    for group in execution_groups:
        # Execute group in parallel
        group_results = await asyncio.gather(*[
            execute_task(task) for task in group
        ])
        results.extend(group_results)
    
    return results
```

---

### 2.5 Federated Search (`backend/agent/federated_search.py`)

**Purpose:** Search across multiple data sources and profiles.

**Sources:**
1. **Profile Databases** - MongoDB collections per profile
2. **Cloud Sources** - Google Drive, Dropbox, etc.
3. **Email Sources** - Gmail, Outlook via Airbyte
4. **External** - Web search, URL content

**Result Fusion:**
```python
def federated_rank_fusion(
    results: Dict[str, List[SearchResult]],
    k: int = 60  # RRF constant
) -> List[FederatedResult]:
    """
    Merge results from multiple sources using RRF.
    
    RRF Score = Σ 1/(k + rank_in_source)
    """
    scores = defaultdict(float)
    
    for source, source_results in results.items():
        for rank, result in enumerate(source_results):
            scores[result.id] += 1 / (k + rank + 1)
    
    # Sort by combined score
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

**Deduplication:**
- By content hash (exact duplicates)
- By semantic similarity (near-duplicates)
- Keeps highest-scored version

---

### 2.6 Agent Schemas (`backend/agent/schemas.py`)

**Event Types for Streaming:**
```python
class AgentEventType(str, Enum):
    START = "start"
    THINKING = "thinking"
    PLANNING = "planning"
    SEARCHING = "searching"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TOKEN = "token"
    SOURCE = "source"
    COMPLETE = "complete"
    ERROR = "error"

class AgentEvent(BaseModel):
    type: AgentEventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any]
```

**Trace Model (for UI display):**
```python
class AgentTrace(BaseModel):
    orchestrator_thinking: str
    plan: Optional[TaskPlan]
    tasks: List[TaskExecution]
    synthesis_reasoning: str
    total_duration_ms: int
    model_costs: Dict[str, float]

class TaskExecution(BaseModel):
    task_id: str
    task_type: str
    status: str  # pending, running, completed, failed
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    results_count: int
    error: Optional[str]
```

---

## 3. Ingestion Queue System

### 3.1 Overview

The ingestion queue provides asynchronous, profile-aware document processing.

```
Frontend                    Backend API                 Worker Process
   │                            │                            │
   │  POST /queue/add           │                            │
   │ ─────────────────────────▶ │                            │
   │                            │  Insert to                 │
   │                            │  ingestion_queue           │
   │                            │  collection                │
   │                            │ ──────────────────────────▶│
   │                            │                            │
   │                            │                            │ Poll for
   │                            │                            │ pending jobs
   │  GET /queue/status         │                            │
   │ ─────────────────────────▶ │                            │
   │                            │                            │
   │  ◀──────── SSE ──────────  │◀─────── Updates ────────── │
   │  (progress, logs)          │                            │
```

---

### 3.2 Queue Endpoints (`backend/routers/ingestion_queue.py`)

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/queue/jobs` | List queued jobs |
| POST | `/queue/add` | Add single file/folder |
| POST | `/queue/add-multiple` | Add multiple items |
| GET | `/queue/status/{job_id}` | Get job status |
| POST | `/queue/cancel/{job_id}` | Cancel pending job |
| POST | `/queue/retry/{job_id}` | Retry failed job |
| DELETE | `/queue/clear` | Clear completed jobs |
| GET | `/queue/stats` | Queue statistics |

**Job Schema:**
```python
class QueuedIngestionJob(BaseModel):
    id: str                     # UUID
    profile: str                # Target profile
    documents_folder: str       # Source folder path
    status: JobStatus           # pending, running, completed, failed
    priority: int = 0           # Higher = sooner
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    progress: JobProgress
    error: Optional[str]
    config: IngestionConfig

class JobProgress(BaseModel):
    total_files: int = 0
    processed_files: int = 0
    failed_files: int = 0
    current_file: Optional[str]
    percent: float = 0.0
```

---

### 3.3 Profile Folder Validation

Before queuing a job, the system validates the profile's documents folder:

```python
def validate_profile_folder(profile: Profile) -> tuple[str, bool, str]:
    """
    Validate profile's documents folder exists and is accessible.
    Returns: (folder_path, is_accessible, error_message)
    """
    folders = profile.documents_folders
    if not folders:
        return "", False, "No documents folders configured"
    
    primary_folder = folders[0]
    folder_path = Path(primary_folder)
    
    if not folder_path.exists():
        return primary_folder, False, f"Folder does not exist: {primary_folder}"
    
    if not folder_path.is_dir():
        return primary_folder, False, f"Path is not a directory"
    
    return primary_folder, True, ""
```

---

## 4. Workers Architecture

### 4.1 Ingestion Worker (`backend/workers/ingestion_worker.py`)

**Purpose:** Background process that processes queued ingestion jobs.

**Lifecycle:**
```
┌────────────────────────────────────────────┐
│            Ingestion Worker                │
├────────────────────────────────────────────┤
│ 1. Initialize DB connection                │
│ 2. Load current profile                    │
│ 3. Poll for pending jobs                   │
│ 4. Process job:                            │
│    a. Discover files                       │
│    b. Process each file (parallel)         │
│    c. Generate embeddings (batched)        │
│    d. Store in profile database            │
│    e. Update progress                      │
│ 5. Mark job complete/failed                │
│ 6. Loop to step 3                          │
└────────────────────────────────────────────┘
```

**Configuration:**
```python
class IngestionWorkerConfig:
    poll_interval_seconds: int = 5
    max_concurrent_files: int = 4
    embedding_batch_size: int = 100
    max_retries: int = 3
    job_timeout_minutes: int = 60
```

**Health Endpoint:**
```
GET /api/v1/system/worker/ingestion/health

Response:
{
  "status": "healthy",
  "current_job": "job_123",
  "jobs_completed": 45,
  "jobs_failed": 2,
  "uptime_seconds": 86400,
  "last_poll": "2026-02-19T12:00:00Z"
}
```

---

### 4.2 Sync Worker (`backend/workers/sync_worker.py`)

**Purpose:** Synchronize cloud sources with local knowledge base.

**Sync Types:**
- **Full Sync:** Re-index all files from cloud source
- **Incremental Sync:** Process only changes since last sync
- **Scheduled Sync:** Automatic sync based on cron schedule

**Process Flow:**
```
1. Authenticate with cloud provider
2. Get changes since last sync (delta token)
3. For each changed file:
   a. Download to local cache
   b. Process with Docling
   c. Generate embeddings
   d. Store in MongoDB
4. Handle deleted files (remove from index)
5. Update delta token for next sync
```

---

## 5. Frontend Architecture

### 5.1 React Contexts

**AuthContext (`contexts/AuthContext.tsx`):**
```typescript
interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refreshToken: () => Promise<void>;
}
```

**ThemeContext (`contexts/ThemeContext.tsx`):**
```typescript
interface ThemeContextType {
  theme: 'light' | 'dark' | 'system';
  effectiveTheme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark' | 'system') => void;
}
```

**LanguageContext (`contexts/LanguageContext.tsx`):**
```typescript
interface LanguageContextType {
  language: 'en' | 'de';
  setLanguage: (lang: 'en' | 'de') => void;
}
```

**ToastContext (`contexts/ToastContext.tsx`):**
```typescript
interface ToastContextType {
  toast: {
    success: (message: string) => void;
    error: (message: string) => void;
    info: (message: string) => void;
    warning: (message: string) => void;
  };
}
```

**ChatSidebarContext (`contexts/ChatSidebarContext.tsx`):**
```typescript
interface ChatSidebarContextType {
  sessions: ChatSession[];
  folders: ChatFolder[];
  activeSessionId: string | null;
  createSession: () => Promise<ChatSession>;
  deleteSession: (id: string) => Promise<void>;
  updateSession: (id: string, data: Partial<ChatSession>) => Promise<void>;
  createFolder: (name: string) => Promise<ChatFolder>;
  moveSessionToFolder: (sessionId: string, folderId: string) => Promise<void>;
}
```

**UserPreferencesContext (`contexts/UserPreferencesContext.tsx`):**
```typescript
interface UserPreferences {
  agentMode: 'auto' | 'thinking' | 'fast';
  showAgentTrace: boolean;
  streamResponses: boolean;
  defaultSearchType: 'hybrid' | 'semantic' | 'text';
  resultsPerPage: number;
}
```

---

### 5.2 Custom Hooks

**useLocalStorage (`hooks/useLocalStorage.ts`):**
```typescript
function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void];

// Storage keys
const STORAGE_KEYS = {
  CHAT_INPUT: 'recallhub_chat_input',
  SEARCH_QUERY: 'recallhub_search_query',
  SEARCH_TYPE: 'recallhub_search_type',
  RECENT_SEARCHES: 'recallhub_recent_searches',
  SIDEBAR_WIDTH: 'recallhub_sidebar_width',
  THEME: 'recallhub_theme',
  LANGUAGE: 'recallhub_language',
};
```

**useKeyboardShortcuts (`hooks/useKeyboardShortcuts.ts`):**
```typescript
interface Shortcut {
  key: string;           // e.g., 'k', 'Enter', 'Escape'
  ctrlKey?: boolean;
  metaKey?: boolean;     // Cmd on Mac
  shiftKey?: boolean;
  handler: () => void;
  description: string;
  ignoreInputs?: boolean;
}

function useKeyboardShortcuts(config: { shortcuts: Shortcut[] }): void;

// Global shortcuts
// Ctrl/Cmd+K: Open command palette
// /: Focus search
// Escape: Close modal
// Ctrl/Cmd+Enter: Send message
```

**useClipboard (`hooks/useClipboard.ts`):**
```typescript
interface UseClipboardReturn {
  copied: boolean;
  copy: (text: string) => Promise<void>;
  error: Error | null;
}

function useClipboard(): UseClipboardReturn;
```

**useSelection (`hooks/useSelection.tsx`):**
```typescript
interface UseSelectionReturn<T> {
  selected: Set<T>;
  toggle: (item: T) => void;
  selectAll: (items: T[]) => void;
  clearSelection: () => void;
  isSelected: (item: T) => boolean;
}

function useSelection<T>(): UseSelectionReturn<T>;
```

---

### 5.3 Key Components

**FederatedAgentPanel (`components/FederatedAgentPanel.tsx`):**
- Displays agent thinking process
- Shows task execution timeline
- Lists sources found
- Expandable/collapsible sections

**FolderPicker (`components/FolderPicker.tsx`):**
- Tree-based folder navigation
- Lazy-loading of subfolders
- Multi-select with checkboxes
- Breadcrumb navigation

**StreamingIndicator (`components/StreamingIndicator.tsx`):**
- Pulsing dot during streaming
- Elapsed time counter
- Current phase display
- Tokens per second

**CopyButton (`components/CopyButton.tsx`):**
- Clipboard copy with feedback
- Configurable success message
- Accessible button with icon

**MarkdownRenderer (`components/MarkdownRenderer.tsx`):**
- Syntax highlighting for code
- Table rendering
- Link handling
- Custom styling

---

### 5.4 Page Summary

| Page | Path | Purpose | Admin |
|------|------|---------|-------|
| Dashboard | `/` | Overview, quick actions | No |
| Chat | `/chat/:id?` | AI conversation interface | No |
| Search | `/search` | Direct document search | No |
| Documents | `/documents` | Browse indexed documents | No |
| Document Preview | `/documents/:id` | View document content | No |
| Configuration | `/configuration` | LLM/embedding settings | Yes |
| Profiles | `/profiles` | Profile management | Yes |
| Search Indexes | `/indexes` | Index status and creation | Yes |
| Local LLM | `/local-llm` | Offline mode settings | Yes |
| Cloud Sources | `/cloud-sources` | Cloud provider dashboard | No |
| Cloud Connect | `/cloud-sources/connect/:type` | OAuth connection flow | No |
| Email Config | `/cloud-sources/email/:type` | Email source setup | No |
| User Management | `/admin/users` | User CRUD, access control | Yes |
| Ingestion | `/admin/ingestion` | Document ingestion queue | Yes |
| Prompts | `/admin/prompts` | Prompt template management | Yes |
| API Keys | `/api-keys` | Personal API key management | No |
| Status | `/status` | System health dashboard | No |
| Login | `/login` | Authentication | No |

---

## 6. i18n System

### 6.1 Structure

```
frontend/src/i18n/
├── index.ts          # i18next initialization
└── locales/
    ├── en.json       # English translations
    └── de.json       # German translations
```

### 6.2 Configuration

```typescript
// i18n/index.ts
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: require('./locales/en.json') },
    de: { translation: require('./locales/de.json') },
  },
  lng: localStorage.getItem('recallhub_language') || 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
});
```

### 6.3 Translation Categories

| Category | Key Prefix | Description |
|----------|------------|-------------|
| Navigation | `nav.*` | Sidebar and header navigation |
| Common | `common.*` | Shared UI elements |
| Auth | `auth.*` | Login, logout, registration |
| Chat | `chat.*` | Chat interface |
| Search | `search.*` | Search page |
| Documents | `documents.*` | Document management |
| Ingestion | `ingestion.*` | Ingestion queue |
| Profiles | `profiles.*` | Profile management |
| Settings | `settings.*` | Configuration pages |
| Errors | `errors.*` | Error messages |
| Empty States | `emptyStates.*` | Empty state messages |

### 6.4 Usage Pattern

```tsx
import { useTranslation } from 'react-i18next';

function MyComponent() {
  const { t } = useTranslation();
  
  return (
    <div>
      <h1>{t('nav.dashboard')}</h1>
      <p>{t('common.loading')}</p>
      <button>{t('common.save')}</button>
    </div>
  );
}
```

### 6.5 Language Switching

```tsx
// LanguageSwitcher component
function LanguageSwitcher() {
  const { i18n } = useTranslation();
  
  const handleChange = (lang: 'en' | 'de') => {
    i18n.changeLanguage(lang);
    localStorage.setItem('recallhub_language', lang);
  };
  
  return (
    <select value={i18n.language} onChange={(e) => handleChange(e.target.value)}>
      <option value="en">English</option>
      <option value="de">Deutsch</option>
    </select>
  );
}
```

---

## 7. Docker Deployment Strategy

### 7.1 Service Architecture

```yaml
# docker-compose.yml services
services:
  mongodb:         # Database
  backend:         # FastAPI application
  ingestion-worker: # Background job processor
  frontend:        # React application (Nginx)
```

### 7.2 Image Strategy

**Two-Stage Build:**

1. **Base Image** (`Dockerfile.base`):
   - Python runtime
   - System dependencies (poppler, tesseract)
   - ML libraries (torch, transformers)
   - Cached for faster rebuilds

2. **Application Image** (`Dockerfile`):
   - FROM base image
   - Application code only
   - Fast rebuild (~30 seconds)

### 7.3 Volume Mounts

```yaml
volumes:
  # Document sources
  - ./documents:/app/documents:ro
  - ./projects:/app/projects:ro
  
  # Profile-specific mounts
  - ./mounts/parhelion-energy:/app/mounts/parhelion-energy:ro
  - ./mounts/gdrive-root:/app/mounts/gdrive-root:ro
  
  # Configuration
  - ./profiles.yaml:/app/profiles.yaml:ro
  
  # Data persistence
  - mongodb_data:/data/db
```

### 7.4 Environment Files

| File | Purpose |
|------|---------|
| `.env` | Local development defaults |
| `.env.docker` | Docker-specific overrides |
| `.env.production` | Production settings |

### 7.5 Development Workflow

```bash
# Build base image (one-time, or when dependencies change)
docker-compose build backend-base

# Start all services
docker-compose up -d

# Rebuild backend only (fast)
docker-compose build backend
docker-compose up -d backend

# View logs
docker-compose logs -f backend

# Restart specific service
docker-compose restart ingestion-worker
```

### 7.6 Health Checks

```yaml
backend:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/system/health"]
    interval: 30s
    timeout: 10s
    retries: 3

ingestion-worker:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
    interval: 30s
    timeout: 10s
    retries: 3
```

---

## 8. Additional Database Collections

### 8.1 `ingestion_queue` Collection

```javascript
{
  "_id": "uuid",
  "profile": "string",            // Target profile key
  "documents_folder": "string",   // Source folder path
  "status": "pending|running|completed|failed|cancelled",
  "priority": 0,                  // Higher = sooner
  "created_at": ISODate,
  "started_at": ISODate,
  "completed_at": ISODate,
  "progress": {
    "total_files": 0,
    "processed_files": 0,
    "failed_files": 0,
    "current_file": "string",
    "percent": 0.0
  },
  "config": {
    "enable_ocr": true,
    "enable_audio": true,
    "chunk_size": 1000,
    "chunk_overlap": 200
  },
  "error": "string",
  "errors": ["array of file-level errors"]
}
```

### 8.2 `model_versions` Collection

```javascript
{
  "_id": "provider:model_id",
  "provider": "openai|google|anthropic|ollama",
  "model_id": "gpt-4o",
  "display_name": "GPT-4o",
  "description": "Most capable GPT-4 model",
  "context_window": 128000,
  "max_output_tokens": 16384,
  "input_price_per_million": 2.50,
  "output_price_per_million": 10.00,
  "supports_vision": true,
  "supports_streaming": true,
  "supports_function_calling": true,
  "supports_json_mode": true,
  "training_data_cutoff": "2024-04",
  "last_updated": ISODate,
  "is_available": true
}
```

### 8.3 `worker_status` Collection

```javascript
{
  "_id": "worker_type",           // ingestion, sync
  "status": "healthy|unhealthy|unknown",
  "current_job_id": "uuid",
  "jobs_completed": 0,
  "jobs_failed": 0,
  "started_at": ISODate,
  "last_heartbeat": ISODate,
  "last_error": "string",
  "metrics": {
    "avg_job_duration_ms": 0,
    "files_processed_total": 0,
    "chunks_created_total": 0
  }
}
```

### 8.4 `sync_state` Collection

```javascript
{
  "_id": "connection_id",
  "provider_type": "google_drive|dropbox|etc",
  "delta_token": "string",        // Provider-specific cursor
  "last_sync_at": ISODate,
  "last_sync_status": "success|partial|failed",
  "files_indexed": 0,
  "total_size_bytes": 0,
  "sync_history": [{
    "started_at": ISODate,
    "completed_at": ISODate,
    "files_added": 0,
    "files_updated": 0,
    "files_deleted": 0,
    "errors": []
  }]
}
```

---

## 9. Legacy Agent Strategies System

> **Current architecture:** The primary strategy subsystem is now **Strategy OS** — a config-driven DAG runtime documented in detail under [07-STRATEGY_OS_OVERVIEW.md](./07-STRATEGY_OS_OVERVIEW.md), [08-STRATEGY_DAG_AND_NODES.md](./08-STRATEGY_DAG_AND_NODES.md), [09-STRATEGY_SPECS_AND_GOVERNANCE.md](./09-STRATEGY_SPECS_AND_GOVERNANCE.md), [10-EVALUATION_AND_JUDGE.md](./10-EVALUATION_AND_JUDGE.md), and [11-EXPLORATION_AND_SCHEDULER.md](./11-EXPLORATION_AND_SCHEDULER.md).
>
> The legacy `BaseStrategy` system described in this section still lives in `backend/agent/strategies/` (note the plural) and remains reachable from `/api/v1/strategies/...` and through the `legacy_orchestrator_pipeline` node inside a Strategy OS spec. New behavior should be authored as a Strategy OS spec, not as a new `BaseStrategy` subclass.

### 9.1 Overview

The legacy Agent Strategies system provides configurable execution patterns for different use cases and domains. Each strategy defines custom prompts, parameters, and behavior for the original orchestrator pipeline.

**Purpose:**
- Enable A/B testing of different agent approaches
- Optimize performance for specific query domains
- Provide fine-grained control over orchestrator behavior
- Track and measure strategy effectiveness

---

### 9.2 Strategy Architecture

```
User Query
    │
    ▼
Strategy Selection
    │
    ├── Auto-Detection ──▶ Analyze query intent
    │                      Extract domain keywords
    │                      Select best-matching strategy
    │
    ├── Manual Selection ─▶ User-specified strategy
    │
    └── Default ──────────▶ Fallback strategy
            │
            ▼
    Load Strategy Config
            │
            ├── Prompts (Analyze, Plan, Evaluate, Synthesize)
            ├── Parameters (max_iterations, confidence_threshold, etc.)
            └── Domain Rules
            │
            ▼
    Execute with Strategy
```

---

### 9.3 Strategy Registry (`backend/agent/strategies/registry.py`)

**Purpose:** Central catalog for all available strategies with auto-discovery.

**Registration Pattern:**
```python
@StrategyRegistry.register
class MyStrategy(BaseStrategy):
    @property
    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            id="my_strategy",
            name="My Custom Strategy",
            version="1.0.0",
            description="Optimized for complex technical queries",
            domains=[StrategyDomain.SOFTWARE_DEV],
            tags=["technical", "code", "detailed"],
            is_default=False,
            author="Team"
        )
    
    @property
    def config(self) -> StrategyConfig:
        return StrategyConfig(
            max_iterations=3,
            confidence_threshold=0.8,
            early_exit_enabled=True,
            cross_search_boost=1.2,
            content_length_penalty=0.1
        )
```

**Key Methods:**
| Method | Description |
|--------|-------------|
| `get(strategy_id)` | Retrieve strategy by ID |
| `list_strategies(domain?)` | List strategies, optionally filtered by domain |
| `get_default()` | Get default strategy |
| `auto_detect(query)` | Auto-detect strategy from query |
| `compare(strategy_a, strategy_b)` | Compare two strategies |

---

### 9.4 Strategy Base Class (`backend/agent/strategies/base.py`)

**Strategy Metadata Schema:**
```python
class StrategyMetadata:
    id: str                      # Unique identifier
    name: str                    # Display name
    version: str                 # Version string
    description: str             # Purpose description
    domains: List[StrategyDomain] # Applicable domains
    tags: List[str]              # Search tags
    is_default: bool             # Is this the default?
    is_legacy: bool              # Deprecated?
    author: str                  # Creator
```

**Strategy Configuration:**
```python
class StrategyConfig:
    max_iterations: int          # Maximum orchestrator loops
    confidence_threshold: float   # Early exit threshold (0.0-1.0)
    early_exit_enabled: bool      # Allow early termination
    cross_search_boost: float     # Boost for cross-profile results
    content_length_penalty: float # Penalty for verbose responses
    custom_params: dict           # Strategy-specific parameters
```

**Strategy Domains:**
| Domain | Description |
|--------|-------------|
| `general` | General-purpose knowledge retrieval |
| `software_dev` | Software development queries |
| `legal` | Legal document analysis |
| `hr` | Human resources queries |

---

### 9.5 Strategy Prompts

Each strategy defines custom prompts for the four orchestrator phases:

**1. Analyze Prompt:**
```python
"""Analyze the user's query to understand intent and extract key entities.

Query: {query}
Domain: {domain}

Tasks:
1. Identify the primary intent
2. Extract key entities and concepts
3. Determine which sources are most relevant
4. Assess query complexity

Output JSON:
{
  "intent": "...",
  "entities": [...],
  "suggested_sources": [...],
  "complexity": "simple|moderate|complex"
}
"""
```

**2. Plan Prompt:**
```python
"""Create an execution plan based on the analysis.

Analysis: {analysis}
Strategy: {strategy_name}

Constraints:
- Max iterations: {max_iterations}
- Available sources: {sources}

Create a plan with parallel tasks where possible.
"""
```

**3. Evaluate Prompt:**
```python
"""Evaluate the quality and completeness of retrieved results.

Results: {results_summary}
Original Query: {query}

Assessment Criteria:
1. Relevance to query intent
2. Coverage of key entities
3. Information freshness
4. Source credibility

Rate quality 0-100 and identify gaps.
"""
```

**4. Synthesize Prompt:**
```python
"""Generate final response from all gathered information.

Query: {query}
All Results: {aggregated_results}
Evaluation: {evaluation}

Requirements:
- Answer the original query directly
- Cite specific sources
- Acknowledge uncertainties
- Keep concise but complete
"""
```

---

### 9.6 Strategy Metrics & A/B Testing

**Metrics Collection (`backend/agent/strategies/metrics.py`):**
```python
class StrategyMetrics:
    strategy_id: str
    execution_count: int
    avg_latency_ms: float
    median_latency_ms: float
    avg_iterations: float
    avg_confidence_score: float
    quality_score: float  # 0-100
    user_feedback_avg: Optional[float]
    feedback_count: int
```

**A/B Test Flow:**
```
1. Split traffic between Strategy A and Strategy B
2. Execute both strategies on similar queries
3. Collect metrics:
   - Latency
   - Iterations
   - Confidence scores
   - Quality scores (LLM-evaluated)
   - User feedback (1-5 rating)
4. Statistical comparison
5. Declare winner with confidence level
```

**LLM-Based Response Comparison:**
```python
# Endpoint: POST /api/v1/strategies/ab-compare-responses

Request:
{
  "query": "...",
  "response_a": "...",
  "response_b": "...",
  "sources_used": [...]
}

Response:
{
  "winner": "a|b|tie",
  "confidence": "high|medium|low",
  "reasoning": "...",
  "quality_scores": {
    "a": 85,
    "b": 78
  }
}
```

---

### 9.7 Strategy API Endpoints (`backend/routers/strategies.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/strategies` | List all strategies |
| GET | `/strategies?domain={domain}` | Filter by domain |
| GET | `/strategies/default` | Get default strategy |
| GET | `/strategies/{id}` | Get strategy details |
| GET | `/strategies/{id}/metrics` | Get performance metrics |
| GET | `/strategies/for-domain/{domain}` | Get best for domain |
| POST | `/strategies/auto-detect` | Detect from query |
| POST | `/strategies/compare` | Compare two strategies |
| POST | `/strategies/feedback` | Record user feedback |
| POST | `/strategies/ab-compare-responses` | LLM response comparison |

---

### 9.8 Built-in Strategies

**1. General Purpose (Default)**
```python
metadata = StrategyMetadata(
    id="general_purpose",
    name="General Purpose",
    is_default=True,
    domains=[StrategyDomain.GENERAL]
)
config = StrategyConfig(
    max_iterations=3,
    confidence_threshold=0.75,
    early_exit_enabled=True
)
```

**2. Software Development**
```python
metadata = StrategyMetadata(
    id="software_dev",
    name="Software Development",
    domains=[StrategyDomain.SOFTWARE_DEV],
    tags=["code", "technical", "api"]
)
config = StrategyConfig(
    max_iterations=4,
    confidence_threshold=0.8,
    cross_search_boost=1.3
)
```

**3. Legal Documents**
```python
metadata = StrategyMetadata(
    id="legal",
    name="Legal Analysis",
    domains=[StrategyDomain.LEGAL],
    tags=["legal", "contracts", "compliance"]
)
config = StrategyConfig(
    max_iterations=5,
    confidence_threshold=0.85,
    content_length_penalty=0.05  # Allow more detail
)
```

**4. HR Queries**
```python
metadata = StrategyMetadata(
    id="hr",
    name="HR Assistant",
    domains=[StrategyDomain.HR],
    tags=["hr","policies", "employee"]
)
config = StrategyConfig(
    max_iterations=3,
    confidence_threshold=0.7
)
```

---

### 9.9 Strategy Selection Logic

**Auto-Detection Algorithm:**
```python
def auto_detect_strategy(query: str) -> str:
    """Detect best strategy from query."""
    
    # Extract keywords and entities
    keywords = extract_keywords(query)
    
    # Domain matching scores
    domain_scores = {
        StrategyDomain.SOFTWARE_DEV: 0.0,
        StrategyDomain.LEGAL: 0.0,
        StrategyDomain.HR: 0.0,
        StrategyDomain.GENERAL: 0.0
    }
    
    # Score based on keyword matches
    for keyword in keywords:
        if keyword in ["code", "api", "function", "bug"]:
            domain_scores[StrategyDomain.SOFTWARE_DEV] += 1.0
        elif keyword in ["contract", "clause", "legal", "compliance"]:
            domain_scores[StrategyDomain.LEGAL] += 1.0
        # ... more rules
    
    # Select highest scoring domain
    best_domain = max(domain_scores, key=domain_scores.get)
    
    # Get best strategy for that domain
    return get_best_strategy_for_domain(best_domain)
```

---

## 10. File Registry Service

### 10.1 Overview

The File Registry tracks the processing status of all files in profile documents folders, enabling selective re-ingestion and providing visibility into file processing history.

**Purpose:**
- Track which files have been processed
- Enable selective re-ingestion based on status
- Prevent redundant processing
- Provide ingestion analytics

---

### 10.2 File Registry Schema (`backend/models/schemas.py`)

```python
class FileRegistryEntry(BaseModel):
    _id: ObjectId
    file_path: str              # Full file path
    profile_key: str            # Associated profile
    classification: FileClassification  # Status enum
    file_hash: str              # SHA-256 hash
    file_size: int              # Bytes
    last_processed_at: datetime
    error_message: Optional[str]
    chunks_created: int
    processing_time_ms: int
```

**File Classifications:**
| Classification | Description | Retry Action |
|----------------|-------------|--------------|
| `pending` | Not yet processed | Auto-process |
| `completed` | Successfully processed | None needed |
| `failed` | Processing failed | Retry with error fix |
| `timeout` | Processing timed out | Retry with more time |
| `image_only_pdf` | PDF with only images | OCR needed |
| `no_chunks` | Processed but no chunks | Manual review |
| `excluded` | Explicitly excluded | Skip always |

---

### 10.3 File Registry Service (`backend/services/file_registry.py`)

**Key Operations:**

**1. Register or Update File:**
```python
async def register_file(
    file_path: str,
    profile_key: str,
    file_hash: str,
    file_size: int
) -> FileRegistryEntry:
    """Register new file or update existing entry."""
```

**2. Mark Processing Status:**
```python
async def mark_completed(path: str, chunks: int, duration_ms: int):
    """Mark file as successfully processed."""

async def mark_failed(path: str, error: str):
    """Mark file as failed with error message."""

async def mark_timeout(path: str):
    """Mark file as timed out."""

async def mark_no_chunks(path: str):
    """Mark file that created no chunks."""
```

**3. Query Files by Status:**
```python
async def get_files_by_classification(
    classification: FileClassification,
    profile_key: Optional[str] = None,
    limit: int = 100
) -> List[FileRegistryEntry]:
```

**4. Selective Re-ingestion:**
```python
async def mark_for_retry(
    classifications: List[FileClassification],
    profile_key: str
) -> int:
    """Mark files for retry based on classification."""
```

---

### 10.4 File Registry API (`backend/routers/file_registry.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/file-registry/stats` | Statistics by classification |
| GET | `/file-registry/files` | List files with filters |
| POST | `/file-registry/reclassify/{path}` | Manually reclassify |
| DELETE | `/file-registry/clear` | Clear entries |
| POST | `/file-registry/retry/category` | Retry by category |

**Stats Response:**
```json
{
  "total_files": 1250,
  "by_classification": {
    "completed": 1180,
    "failed": 25,
    "timeout": 15,
    "image_only_pdf": 20,
    "no_chunks": 10,
    "pending": 0
  },
  "profile_breakdown": {
    "default": 800,
    "parhelion": 450
  }
}
```

---

### 10.5 Integration with Ingestion

**Ingestion Flow with Registry:**
```
1. Scan documents folder
2. For each file:
   a. Check registry for existing entry
   b. If completed and hash unchanged → Skip
   c. If failed/timeout → Optionally retry
   d. Process file
   e. Update registry with result
3. Report skipped vs processed
```

**Change Detection:**
```python
async def should_process_file(file_path: str) -> bool:
    """Check if file needs processing."""
    
    entry = await registry.get(file_path)
    
    if not entry:
        return True  # New file
    
    if entry.classification == "excluded":
        return False  # Explicitly excluded
    
    current_hash = compute_hash(file_path)
    
    if current_hash != entry.file_hash:
        return True  # File changed
    
    if entry.classification in ["failed", "timeout"]:
        return True  # Retry needed
    
    return False  # Already processed successfully
```

---

## 11. Backup Service

### 11.1 Overview

The Backup Service provides comprehensive backup and restore capabilities for MongoDB collections with support for full, incremental, and checkpoint backups.

**Purpose:**
- Protect against data loss
- Enable point-in-time recovery
- Support selective restore
- Minimize storage requirements

---

### 11.2 Backup Types

| Type | Description | Storage | Use Case |
|------|-------------|---------|----------|
| `full` | Complete collection dump | Large | Weekly scheduled |
| `incremental` | Changes since last backup | Small | Daily/hourly |
| `checkpoint` | Lightweight state snapshot | Minimal | Before risky operations |
| `post_ingestion` | Auto-backup after ingestion | Medium | Data protection |

---

### 11.3 Backup Architecture

```
Backup Chain (Incremental):
Full Backup (Base)
    └── Incremental 1 (changes from Full)
            └── Incremental 2 (changes from Inc 1)
                    └── Incremental 3 (latest)

Restore Process:
1. Restore Full Backup
2. Apply Incremental 1 changes
3. Apply Incremental 2 changes
4. Apply Incremental 3 changes
5. Result: State at Incremental 3
```

---

### 11.4 Backup Service (`backend/services/backup_service.py`)

**Core Methods:**

**1. Create Full Backup:**
```python
async def create_full_backup(
    profile_key: str,
    include_embeddings: bool = False,
    compress: bool = True
) -> BackupMetadata:
    """Create complete backup of profile collections."""
```

**2. Create Incremental Backup:**
```python
async def create_incremental_backup(
    profile_key: str,
    parent_backup_id: str
) -> BackupMetadata:
    """Create incremental backup from parent."""
```

**3. Create Checkpoint:**
```python
async def create_checkpoint(
    name: str,
    description: str
) -> BackupMetadata:
    """Create lightweight checkpoint."""
```

**4. Restore:**
```python
async def restore_from_backup(
    backup_id: str,
    mode: RestoreMode = "full",
    collections: Optional[List[str]] = None,
    skip_users: bool = False,
    skip_sessions: bool = False
) -> RestoreResult:
    """Restore from backup."""
```

---

### 11.5 Backup Schemas (`backend/models/backup_schemas.py`)

**Backup Metadata:**
```python
class BackupMetadata(BaseModel):
    _id: str                     # UUID
    backup_type: BackupType      # full/incremental/checkpoint
    profile_key: str
    name: str
    description: str
    status: BackupStatus         # pending/running/completed/failed
    file_path: str               # Path to backup file
    file_size_bytes: int
    created_at: datetime
    completed_at: Optional[datetime]
    parent_backup_id: Optional[str]  # For incrementals
    collections: List[CollectionInfo]
    include_embeddings: bool
    compressed: bool
    checksum: str                # SHA-256
```

**Restore Options:**
```python
class RestoreOptions(BaseModel):
    mode: RestoreMode            # full/merge/selective
    collections: Optional[List[str]]
    skip_users: bool = False
    skip_sessions: bool = False
    dry_run: bool = False
```

---

### 11.6 Backup API (`backend/routers/backup.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/backups/create` | Create full backup |
| POST | `/backups/checkpoint` | Create checkpoint |
| GET | `/backups/` | List all backups |
| GET | `/backups/checkpoints` | List checkpoints |
| GET | `/backups/{id}` | Get backup details |
| GET | `/backups/{id}/chain` | Get backup chain |
| POST | `/backups/{id}/restore` | Restore from backup |
| DELETE | `/backups/{id}` | Delete backup |
| GET | `/backups/config` | Get backup config |
| PUT | `/backups/config` | Update backup config |
| GET | `/backups/status` | Get operation status |
| GET | `/backups/storage` | Get storage stats |

---

### 11.7 Backup Configuration

```python
class BackupConfig(BaseModel):
    auto_backup_after_ingestion: bool = True
    retention_days: int = 30
    max_backups_per_profile: int = 10
    compression_enabled: bool = True
    include_embeddings: bool = False  # Can regenerate
    backup_schedule: str = "0 0 * * 0"  # Weekly
    backup_directory: str = "./backups"
```

---

### 11.8 Restore Modes

**Full Restore:**
```python
# Replace all data with backup data
collections_to_restore = ["documents", "chunks"]
options = RestoreOptions(
    mode="full",
    collections=collections_to_restore,
    skip_users=True,
    skip_sessions=True
)
```

**Merge Restore:**
```python
# Add missing documents only (no overwrites)
options = RestoreOptions(
    mode="merge",
    collections=["documents", "chunks"]
)
```

**Selective Restore:**
```python
# Restore specific collections only
options = RestoreOptions(
    mode="selective",
    collections=["documents"]  # Only documents, not chunks
)
```

---

## 12. Embedding Benchmark Service

### 12.1 Overview

The Embedding Benchmark Service enables comparison of different embedding providers and models to find the optimal choice for your use case.

**Purpose:**
- Compare embedding providers (OpenAI, Ollama, vLLM)
- Measure performance metrics
- Estimate costs
- Select best provider for requirements

---

### 12.2 Supported Providers

| Provider | Type | Models |
|----------|------|--------|
| OpenAI | Cloud API | text-embedding-3-small, ada-002 |
| Ollama | Local | nomic-embed-text, all-minilm |
| vLLM | Self-hosted | Custom models |

---

### 12.3 Benchmark Architecture

```
Benchmark Execution:
1. Upload test document
2. Configure providers to compare (max 3)
3. For each provider:
   a. Read document
   b. Chunk text (configurable chunk_size/overlap)
   c. Generate embeddings (batched)
   d. Measure timing and memory
   e. Calculate cost estimate
4. Aggregate results
5. Declare winner based on criteria
```

---

### 12.4 Benchmark Metrics

**Performance Metrics:**
| Metric | Description | Importance |
|--------|-------------|------------|
| `total_time_ms` | End-to-end processing | High |
| `chunking_time_ms` | Text chunking time | Medium |
| `embedding_time_ms` | Embedding generation | High |
| `avg_latency_ms` | Average per-chunk latency | Medium |
| `tokens_processed` | Total tokens embedded | High |
| `embedding_dimension` | Vector dimensions | High |
| `memory_peak_mb` | Peak memory usage | Medium |
| `cost_estimate_usd` | Estimated cost | High |

---

### 12.5 Benchmark Service (`backend/services/embedding_benchmark.py`)

**Run Benchmark:**
```python
async def run_benchmark(
    file_content: str,
    file_name: str,
    providers: List[str],
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    max_tokens: int = 512
) -> BenchmarkResult:
    """Run benchmark across multiple providers."""
```

**Test Provider:**
```python
async def test_provider(provider: str, model: str) -> ProviderTestResult:
    """Test provider connectivity and basic functionality."""
```

---

### 12.6 Benchmark API (`backend/routers/embedding_benchmark.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/benchmark/run` | Run benchmark |
| POST | `/benchmark/run-file` | Run with file upload |
| GET | `/benchmark/providers` | Get available providers |
| POST | `/benchmark/test-provider` | Test provider |
| GET | `/benchmark/results` | Get historical results |
| GET | `/benchmark/results/{id}` | Get specific result |
| DELETE | `/benchmark/results/{id}` | Delete result |

---

### 12.7 Benchmark Result Schema

```python
class BenchmarkResult(BaseModel):
    _id: str                     # UUID
    timestamp: datetime
    file_name: str
    file_size_bytes: int
    content_preview: str
    chunk_config: ChunkConfig
    results: List[ProviderResult]
    winner: str                  # Best provider

class ProviderResult(BaseModel):
    provider: str
    model: str
    provider_type: str           # cloud/local/self-hosted
    total_time_ms: float
    embedding_time_ms: float
    avg_latency_ms: float
    tokens_processed: int
    chunks_created: int
    embedding_dimension: int
    memory_peak_mb: float
    cost_estimate_usd: float
    success: bool
    error: Optional[str]
```

---

### 12.8 Winner Selection Logic

**Scoring Algorithm:**
```python
def select_winner(results: List[ProviderResult]) -> str:
    """Select best provider based on weighted scoring."""
    
    scores = {}
    
    for result in results:
        if not result.success:
            continue
        
        # Weighted scoring
        score = (
            (1000 / result.total_time_ms) * 0.3 +      # Speed
            (result.cost_estimate_usd * -1) * 0.4 +    # Cost (lower=better)
            (result.embedding_dimension / 100) * 0.2 + # Dimension
            (100 / result.avg_latency_ms) * 0.1        # Latency
        )
        
        scores[result.provider] = score
    
    return max(scores, key=scores.get)
```

---

## Document Version

**Version:** 1.0.0  
**Last Updated:** 2026-02-19  
**Status:** Complete

---

*This document supplements [03-PROJECT_DOCUMENTATION.md](./03-PROJECT_DOCUMENTATION.md) with detailed technical blueprints for all system components.*
