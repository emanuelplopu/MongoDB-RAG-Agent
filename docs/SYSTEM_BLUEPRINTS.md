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
├── async_client (AsyncIOMotorClient)
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

## Document Version

**Version:** 1.0.0  
**Last Updated:** 2026-02-19  
**Status:** Complete

---

*This document supplements PROJECT_DOCUMENTATION.md with detailed technical blueprints for all system components.*
