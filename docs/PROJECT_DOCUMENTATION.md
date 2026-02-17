# RecallHub - Complete Project Documentation

## Table of Contents
1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Database Schema](#3-database-schema)
4. [API Endpoints](#4-api-endpoints)
5. [Configuration](#5-configuration)
6. [Authentication & Authorization](#6-authentication--authorization)
7. [Agent System](#7-agent-system)
8. [Document Ingestion](#8-document-ingestion)
9. [Cloud Source Providers](#9-cloud-source-providers)
10. [Frontend Pages](#10-frontend-pages)
11. [Environment Variables](#11-environment-variables)

---

## 1. Project Overview

**RecallHub** is an intelligent knowledge base search system built with MongoDB Atlas Vector Search. It provides RAG (Retrieval Augmented Generation) capabilities with a federated agent architecture.

### Key Features
- **Hybrid Search**: Combines semantic vector search with full-text keyword search using Reciprocal Rank Fusion (RRF)
- **Multi-Format Ingestion**: PDF, Word, PowerPoint, Excel, HTML, Markdown, Images (OCR), Audio transcription (Whisper)
- **Intelligent Chunking**: Docling HybridChunker preserves document structure and semantic boundaries
- **Federated Agent**: Orchestrator-Worker architecture for complex query handling
- **Multiple LLM Support**: OpenAI, Google Gemini, Anthropic Claude, Ollama (local)
- **Multi-Profile Support**: Isolated workspaces with separate databases and document folders
- **Cloud Source Integration**: Google Drive, Dropbox, OneDrive, Confluence, Jira, Gmail, Outlook

### Technology Stack
| Component | Technology |
|-----------|------------|
| Database | MongoDB Atlas (Vector Search + Full-Text Search) |
| Backend | FastAPI, Python 3.10+ |
| Frontend | React, TypeScript, Vite, TailwindCSS |
| Agent Framework | Pydantic AI, LiteLLM |
| Document Processing | Docling 2.14+ |
| Audio Transcription | OpenAI Whisper (local) |
| Embeddings | OpenAI text-embedding-3-small (default) |

---

## 2. Architecture

### System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │  Chat    │ │  Search  │ │ Profiles │ │Ingestion │ │  Admin   │  │
│  │  Page    │ │  Page    │ │  Page    │ │  Page    │ │  Pages   │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    API Routers                               │   │
│  │  /chat  /search  /profiles  /ingestion  /auth  /sessions    │   │
│  │  /system  /indexes  /prompts  /cloud-sources  /local-llm    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                    │                                │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │               Federated Agent System                         │   │
│  │  ┌─────────────┐       ┌─────────────────────────┐          │   │
│  │  │ Orchestrator│──────▶│     Worker Pool         │          │   │
│  │  │  (Thinking) │       │  (Parallel Execution)   │          │   │
│  │  └─────────────┘       └─────────────────────────┘          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                    │                                │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Core Services                             │   │
│  │  DatabaseManager  ProfileManager  LLMProviders  FileCache   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      MongoDB Atlas                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐              │
│  │documents │ │  chunks  │ │  users   │ │ sessions │              │
│  │collection│ │collection│ │collection│ │collection│              │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘              │
│                    │                                                │
│         ┌─────────┴─────────┐                                      │
│    ┌────┴────┐        ┌─────┴─────┐                                │
│    │ Vector  │        │   Text    │                                │
│    │  Index  │        │   Index   │                                │
│    └─────────┘        └───────────┘                                │
└─────────────────────────────────────────────────────────────────────┘
```

### Agent Architecture

```
User Query
    │
    ▼
┌───────────────────────────────────────┐
│          FederatedAgent               │
│  ┌─────────────────────────────────┐  │
│  │        Orchestrator             │  │
│  │  - Analyze user intent          │  │
│  │  - Create execution plan        │  │
│  │  - Evaluate results             │  │
│  │  - Synthesize final response    │  │
│  └─────────────────────────────────┘  │
│              │                        │
│              ▼                        │
│  ┌─────────────────────────────────┐  │
│  │         Worker Pool             │  │
│  │  ┌───────┐ ┌───────┐ ┌───────┐  │  │
│  │  │Worker1│ │Worker2│ │Worker3│  │  │
│  │  │Search │ │Search │ │Web    │  │  │
│  │  │Profile│ │Emails │ │Search │  │  │
│  │  └───────┘ └───────┘ └───────┘  │  │
│  └─────────────────────────────────┘  │
└───────────────────────────────────────┘
    │
    ▼
Final Response + Sources
```

---

## 3. Database Schema

### MongoDB Collections

#### 3.1 `documents` Collection
Stores document metadata for ingested files.

```javascript
{
  "_id": ObjectId,
  "title": String,              // Document title
  "source": String,             // File path/source
  "content_type": String,       // MIME type
  "file_size": Number,          // Size in bytes
  "checksum": String,           // File hash for deduplication
  "created_at": ISODate,
  "updated_at": ISODate,
  "metadata": {
    "author": String,
    "pages": Number,
    "format": String,
    // Additional metadata
  }
}
```

#### 3.2 `chunks` Collection
Stores document chunks with embeddings for vector search.

```javascript
{
  "_id": ObjectId,
  "document_id": ObjectId,      // Reference to documents collection
  "content": String,            // Chunk text content
  "embedding": [Number],        // Vector embedding (1536 dimensions)
  "chunk_index": Number,        // Position in document
  "start_char": Number,
  "end_char": Number,
  "token_count": Number,
  "metadata": {
    "title": String,
    "source": String,
    "chunk_method": String,     // "hybrid" or "simple_fallback"
    "total_chunks": Number
  }
}
```

**Indexes:**
- `vector_index`: Atlas Vector Search index on `embedding` field
- `text_index`: Atlas Search index on `content` field

#### 3.3 `users` Collection
User accounts and authentication.

```javascript
{
  "_id": String,                // UUID
  "email": String,              // Unique, lowercase
  "name": String,
  "password_hash": String,      // bcrypt hash
  "is_active": Boolean,
  "is_admin": Boolean,
  "created_at": ISODate,
  "updated_at": ISODate
}
```

#### 3.4 `api_keys` Collection
API keys for programmatic access.

```javascript
{
  "_id": String,                // UUID
  "user_id": String,            // Owner
  "name": String,               // Key name
  "key_hash": String,           // SHA-256 hash
  "key_prefix": String,         // First 12 chars for display
  "scopes": [String],           // ["read", "write"]
  "is_active": Boolean,
  "created_at": ISODate,
  "last_used_at": ISODate,
  "expires_at": ISODate         // Optional expiration
}
```

#### 3.5 `chat_sessions` Collection
Chat session history and statistics.

```javascript
{
  "_id": String,                // UUID
  "title": String,
  "user_id": String,
  "folder_id": String,          // Optional folder
  "model": String,              // LLM model used
  "profile": String,            // Profile key
  "messages": [{
    "id": String,
    "role": String,             // "user" or "assistant"
    "content": String,
    "timestamp": ISODate,
    "stats": {
      "input_tokens": Number,
      "output_tokens": Number,
      "cost_usd": Number,
      "latency_ms": Number
    },
    "sources": [Object],        // Retrieved documents
    "agent_trace": Object       // Full agent trace
  }],
  "stats": {
    "total_messages": Number,
    "total_tokens": Number,
    "total_cost_usd": Number
  },
  "is_pinned": Boolean,
  "is_archived": Boolean,
  "created_at": ISODate,
  "updated_at": ISODate
}
```

#### 3.6 `chat_folders` Collection
Folder organization for chat sessions.

```javascript
{
  "_id": String,
  "name": String,
  "user_id": String,
  "color": String,              // Hex color
  "is_expanded": Boolean,
  "created_at": ISODate
}
```

#### 3.7 `ingestion_jobs` Collection
Document ingestion job tracking.

```javascript
{
  "_id": String,                // Job UUID
  "status": String,             // pending/running/completed/failed/paused/interrupted
  "started_at": ISODate,
  "completed_at": ISODate,
  "total_files": Number,
  "processed_files": Number,
  "failed_files": Number,
  "document_count": Number,
  "image_count": Number,
  "audio_count": Number,
  "video_count": Number,
  "chunks_created": Number,
  "current_file": String,
  "errors": [String],
  "progress_percent": Number,
  "config": Object,             // Ingestion configuration
  "profile": String
}
```

#### 3.8 `profile_access` Collection
User-profile access control.

```javascript
{
  "_id": ObjectId,
  "user_id": String,
  "profile_key": String,
  "granted_at": ISODate,
  "granted_by": String          // Admin user ID
}
```

#### 3.9 `prompt_templates` Collection
Customizable prompt templates with versioning.

```javascript
{
  "_id": String,
  "name": String,
  "description": String,
  "category": String,           // "chat", "search", etc.
  "versions": [{
    "version": Number,
    "system_prompt": String,
    "tools": [{
      "name": String,
      "description": String,
      "parameters": [Object],
      "enabled": Boolean
    }],
    "created_at": ISODate,
    "created_by": String,
    "notes": String,
    "is_active": Boolean
  }],
  "active_version": Number,
  "created_at": ISODate,
  "updated_at": ISODate
}
```

#### 3.10 `offline_config` Collection
Local LLM configuration for offline mode.

```javascript
{
  "_id": "config",
  "enabled": Boolean,
  "chat_provider": String,
  "chat_model": String,
  "chat_url": String,
  "embedding_provider": String,
  "embedding_model": String,
  "embedding_url": String,
  "vision_provider": String,
  "vision_model": String,
  "vision_url": String,
  "audio_provider": String,
  "audio_model": String,
  "audio_url": String
}
```

#### 3.11 `llm_config` Collection
LLM provider configuration persistence.

```javascript
{
  "_id": "config",
  "orchestrator_provider": String,
  "orchestrator_model": String,
  "worker_provider": String,
  "worker_model": String,
  "embedding_provider": String,
  "embedding_model": String
}
```

---

## 4. API Endpoints

### 4.1 Chat Endpoints (`/api/v1/chat`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/` | Send message and get AI response |
| GET | `/conversations/{id}` | Get conversation history |
| DELETE | `/conversations/{id}` | Delete conversation |
| GET | `/conversations` | List all conversations |

### 4.2 Search Endpoints (`/api/v1/search`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/` | Unified search endpoint |
| POST | `/semantic` | Vector similarity search |
| POST | `/text` | Full-text keyword search |
| POST | `/hybrid` | Combined search with RRF fusion |

### 4.3 Profile Endpoints (`/api/v1/profiles`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List accessible profiles |
| GET | `/active` | Get active profile |
| POST | `/switch` | Switch active profile |
| POST | `/create` | Create new profile (admin) |
| PUT | `/{key}` | Update profile (admin) |
| DELETE | `/{key}` | Delete profile (admin) |
| GET | `/{key}/cloud-sources` | List profile cloud sources |
| POST | `/{key}/cloud-sources` | Add cloud source |
| PUT | `/{key}/cloud-sources/{id}` | Update cloud source |
| DELETE | `/{key}/cloud-sources/{id}` | Remove cloud source |
| GET | `/{key}/airbyte` | Get Airbyte config |
| PUT | `/{key}/airbyte` | Update Airbyte config (admin) |

### 4.4 Session Endpoints (`/api/v1/sessions`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List user's chat sessions |
| POST | `/` | Create new session |
| GET | `/{id}` | Get session with messages |
| PUT | `/{id}` | Update session |
| DELETE | `/{id}` | Delete session |
| POST | `/{id}/messages` | Send message in session |
| GET | `/folders` | List folders |
| POST | `/folders` | Create folder |
| PUT | `/folders/{id}` | Update folder |
| DELETE | `/folders/{id}` | Delete folder |
| POST | `/archive` | Archive sessions |
| POST | `/restore` | Restore archived sessions |
| GET | `/archived/list` | List archived sessions |
| GET | `/{id}/export` | Export session as JSON |

### 4.5 Ingestion Endpoints (`/api/v1/ingestion`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/start` | Start ingestion job |
| GET | `/status` | Get current job status |
| GET | `/status/{id}` | Get specific job status |
| POST | `/cancel/{id}` | Cancel running job |
| POST | `/pause` | Pause running job |
| POST | `/resume` | Resume paused job |
| POST | `/stop` | Stop running job |
| GET | `/jobs` | List all jobs |
| GET | `/runs` | Paginated job history |
| GET | `/pending-files` | Files pending indexing |
| GET | `/logs` | Get ingestion logs |
| GET | `/logs/stream` | Stream logs (SSE) |
| GET | `/documents` | List indexed documents |
| DELETE | `/documents/{id}` | Delete document |

### 4.6 Authentication Endpoints (`/api/v1/auth`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/register` | Register new user |
| POST | `/login` | Login and get JWT |
| POST | `/logout` | Logout |
| GET | `/me` | Get current user |
| PUT | `/me` | Update current user |
| PUT | `/me/password` | Change password |
| GET | `/users` | List users (admin) |
| POST | `/users/create` | Create user (admin) |
| PUT | `/users/{id}` | Update user (admin) |
| PUT | `/users/{id}/status` | Activate/deactivate user |
| DELETE | `/users/{id}` | Delete user (admin) |
| GET | `/access-matrix` | Profile access matrix (admin) |
| POST | `/access` | Set profile access (admin) |
| GET | `/api-keys` | List user's API keys |
| POST | `/api-keys` | Create API key |
| DELETE | `/api-keys/{id}` | Revoke API key |
| PUT | `/api-keys/{id}/toggle` | Enable/disable API key |

### 4.7 System Endpoints (`/api/v1/system`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/stats` | Database statistics |
| GET | `/config` | Current configuration |
| PUT | `/config` | Update configuration |
| GET | `/llm-config` | Get LLM config |
| PUT | `/llm-config` | Update LLM config |

### 4.8 Index Endpoints (`/api/v1/indexes`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List search indexes |
| POST | `/setup` | Create/update indexes |
| GET | `/status` | Index status |

### 4.9 Prompt Management (`/api/v1/prompts`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/templates` | List all templates |
| POST | `/templates` | Create template |
| GET | `/templates/{id}` | Get template |
| PUT | `/templates/{id}` | Update template metadata |
| DELETE | `/templates/{id}` | Delete template |
| POST | `/templates/{id}/versions` | Create new version |
| PUT | `/templates/{id}/versions/{v}/activate` | Activate version |
| POST | `/templates/{id}/test` | Test prompt |
| POST | `/templates/compare` | Compare versions |

### 4.10 Local LLM (`/api/v1/local-llm`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/providers` | Discover local providers |
| GET | `/providers/{id}/models` | List provider models |
| POST | `/test` | Test model |
| GET | `/offline-config` | Get offline config |
| PUT | `/offline-config` | Update offline config |

### 4.11 Cloud Sources (`/api/v1/cloud-sources`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/providers` | List available providers |
| GET | `/connections` | List connections |
| POST | `/connections` | Create connection |
| GET | `/connections/{id}` | Get connection |
| DELETE | `/connections/{id}` | Delete connection |
| GET | `/connections/{id}/folders` | Browse folders |
| POST | `/oauth/authorize` | Start OAuth flow |
| GET | `/oauth/callback` | OAuth callback |
| POST | `/sync/{id}` | Trigger sync |
| GET | `/sync/{id}/status` | Get sync status |

---

## 5. Configuration

### 5.1 Profile Configuration (`profiles.yaml`)

```yaml
active_profile: default

profiles:
  default:
    name: "Default"
    description: "Default project profile"
    documents_folders:
      - "documents"
    database: "rag_db"
    collection_documents: "documents"
    collection_chunks: "chunks"
    vector_index: "vector_index"
    text_index: "text_index"
    embedding_model: null  # Use default
    llm_model: null        # Use default
    airbyte:
      workspace_id: null
      destination_id: null
      default_sync_mode: "incremental"
    cloud_sources: []
  
  my_project:
    name: "My Project"
    description: "Separate project workspace"
    documents_folders:
      - "projects/my-project/documents"
    database: "rag_my_project"
    # ... other settings
```

### 5.2 Backend Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `api_port` | 8000 | API server port |
| `api_workers` | 4 | Number of workers |
| `debug` | false | Debug mode |
| `cors_origins` | ["http://localhost:3000"] | Allowed origins |
| `llm_provider` | "openai" | Primary LLM provider |
| `llm_model` | "gpt-4o" | Primary LLM model |
| `embedding_provider` | "openai" | Embedding provider |
| `embedding_model` | "text-embedding-3-small" | Embedding model |
| `embedding_dimension` | 1536 | Embedding dimensions |
| `orchestrator_model` | "gpt-4o" | Orchestrator model |
| `worker_model` | "gemini-2.0-flash-exp" | Worker model |
| `agent_max_iterations` | 3 | Max orchestrator iterations |
| `agent_parallel_workers` | 4 | Parallel worker tasks |
| `default_match_count` | 10 | Default search results |

---

## 6. Authentication & Authorization

### 6.1 Authentication Methods

1. **JWT Bearer Token**
   - Login returns JWT token
   - Token valid for 7 days
   - Pass in `Authorization: Bearer <token>` header

2. **API Key**
   - Create via API Keys page
   - Pass in `X-API-Key: <key>` header
   - Supports expiration and scopes

### 6.2 Authorization Levels

| Level | Capabilities |
|-------|--------------|
| Anonymous | Limited access (if enabled) |
| User | Access granted profiles, own sessions |
| Admin | All profiles, user management, system config |

### 6.3 Profile Access Control

- Admins have access to all profiles
- Users can only access profiles granted by admin
- Access managed via `profile_access` collection
- Profile switch validates user access

---

## 7. Agent System

### 7.1 Agent Modes

| Mode | Description |
|------|-------------|
| `auto` | Automatically choose based on query complexity |
| `thinking` | Use orchestrator for all queries |
| `fast` | Direct worker execution, skip orchestrator |

### 7.2 Orchestrator Phases

1. **ANALYZE**: Parse user intent, identify entities, determine sources
2. **PLAN**: Create execution plan with tasks
3. **EVALUATE**: Assess results, identify gaps, decide if more iterations needed
4. **SYNTHESIZE**: Generate final response from all gathered information

### 7.3 Task Types

| Type | Description |
|------|-------------|
| `search_profile` | Search current profile database |
| `search_all` | Search across all accessible databases |
| `web_search` | Search the web |
| `browse_url` | Fetch specific URL content |
| `email_search` | Search connected email sources |
| `confluence_search` | Search Confluence spaces |

### 7.4 Worker Execution

- Workers execute tasks in parallel (up to `agent_parallel_workers`)
- Each worker uses the fast model (e.g., Gemini Flash)
- Results aggregated and deduplicated
- RRF fusion for multi-source results

---

## 8. Document Ingestion

### 8.1 Supported Formats

| Category | Formats |
|----------|---------|
| Text | `.txt`, `.md`, `.markdown` |
| Documents | `.pdf`, `.docx`, `.doc`, `.pptx`, `.ppt`, `.xlsx`, `.xls` |
| Web | `.html`, `.htm` |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp` (OCR) |
| Audio | `.mp3`, `.wav`, `.m4a`, `.flac` (Whisper transcription) |
| Video | `.mp4`, `.avi`, `.mkv`, `.mov`, `.webm` (frame extraction) |

### 8.2 Ingestion Pipeline

```
Files Discovery
    │
    ▼
Format Detection
    │
    ├── Text/Docs ──▶ Docling Converter
    ├── Images ────▶ OCR Pipeline
    ├── Audio ─────▶ Whisper Transcription
    └── Video ─────▶ Frame Extraction
    │
    ▼
Markdown Output
    │
    ▼
HybridChunker
    │
    ├── Token-aware splitting
    ├── Structure preservation
    └── Context inclusion
    │
    ▼
Embedding Generation
    │
    ▼
MongoDB Storage
```

### 8.3 Chunking Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `chunk_size` | 1000 | Target characters per chunk |
| `chunk_overlap` | 200 | Character overlap |
| `max_tokens` | 512 | Maximum tokens for embedding |

### 8.4 Ingestion Features

- **Incremental**: Skip already-ingested files (based on checksum)
- **Pause/Resume**: Pause long-running jobs
- **Auto-Resume**: Resume interrupted jobs on restart
- **Progress Tracking**: Real-time progress and ETA
- **Log Streaming**: SSE endpoint for live logs

---

## 9. Cloud Source Providers

### 9.1 Provider Types

| Provider | Auth Type | Integration |
|----------|-----------|-------------|
| Google Drive | OAuth2 | Direct SDK |
| Dropbox | OAuth2 | Direct SDK |
| OneDrive | OAuth2 | Direct SDK |
| OwnCloud/Nextcloud | WebDAV | Direct |
| Confluence | OAuth2/API Key | Airbyte |
| Jira | OAuth2/API Key | Airbyte |
| Gmail | OAuth2 | Airbyte |
| Outlook | OAuth2 | Airbyte |
| IMAP Email | Password | Airbyte |

### 9.2 Provider Capabilities

```python
@dataclass
class ProviderCapabilities:
    provider_type: ProviderType
    display_name: str
    description: str
    supported_auth_types: list[AuthType]
    oauth_scopes: list[str]
    supports_delta_sync: bool
    supports_webhooks: bool
    supports_file_streaming: bool
    supports_folders: bool
    supports_files: bool
    supports_attachments: bool
    rate_limit_requests_per_minute: int
```

### 9.3 Cloud Source Association

Each profile can have multiple cloud sources:

```yaml
cloud_sources:
  - connection_id: "gdrive-work"
    provider_type: "google_drive"
    display_name: "Work Drive"
    enabled: true
    sync_schedule: "0 */6 * * *"  # Every 6 hours
    include_paths:
      - "/Projects"
      - "/Documents"
    exclude_paths:
      - "/Projects/Archive"
    collection_prefix: "gdrive_"
```

---

## 10. Frontend Pages

### 10.1 Main Pages

| Page | Path | Description |
|------|------|-------------|
| Home | `/` | Dashboard with quick actions |
| Chat | `/chat/:id?` | Chat interface with sessions |
| Search | `/search` | Direct search interface |
| Documents | `/documents` | Browse indexed documents |

### 10.2 Configuration Pages

| Page | Path | Description |
|------|------|-------------|
| Configuration | `/configuration` | LLM and system settings |
| Profiles | `/profiles` | Profile management |
| Search Indexes | `/indexes` | Index status and setup |
| Local LLM | `/local-llm` | Offline mode configuration |

### 10.3 Integration Pages

| Page | Path | Description |
|------|------|-------------|
| Cloud Sources | `/cloud-sources` | Cloud provider connections |
| Cloud Connect | `/cloud-sources/connect/:type` | OAuth connection flow |
| Email Config | `/cloud-sources/email/:type` | Email source setup |

### 10.4 Admin Pages

| Page | Path | Description |
|------|------|-------------|
| User Management | `/admin/users` | User CRUD, access control |
| Ingestion | `/admin/ingestion` | Document ingestion control |
| Prompts | `/admin/prompts` | Prompt template management |
| API Keys | `/api-keys` | Personal API key management |
| Status | `/status` | System status dashboard |

---

## 11. Environment Variables

### 11.1 Required Variables

```bash
# MongoDB
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGODB_DATABASE=rag_db

# LLM Providers (at least one)
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...

# JWT Secret (change in production!)
JWT_SECRET_KEY=your-secret-key-here
```

### 11.2 Optional Variables

```bash
# API Configuration
API_PORT=8000
API_WORKERS=4
DEBUG=false

# LLM Configuration
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
ORCHESTRATOR_MODEL=gpt-4o
ORCHESTRATOR_PROVIDER=openai
WORKER_MODEL=gemini-2.0-flash-exp
WORKER_PROVIDER=google

# Embedding Configuration
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536

# Search Configuration
DEFAULT_MATCH_COUNT=10
MAX_MATCH_COUNT=50
DEFAULT_TEXT_WEIGHT=0.3

# Agent Configuration
AGENT_MAX_ITERATIONS=3
AGENT_PARALLEL_WORKERS=4
AGENT_MODE=auto

# Airbyte Integration
AIRBYTE_ENABLED=false
AIRBYTE_API_URL=http://airbyte-server:8001

# Web Search
BRAVE_SEARCH_API_KEY=...

# CORS
CORS_ORIGINS=["http://localhost:3000","http://localhost:8080"]
```

### 11.3 Docker Environment

```bash
# docker-compose.yml environment
MONGODB_URI=mongodb://mongodb:27017/?directConnection=true
AIRBYTE_API_URL=http://airbyte-server:8001
AIRBYTE_MONGODB_HOST=mongodb
```

---

## Appendix A: Search Index Definitions

### Vector Search Index (`vector_index`)

```json
{
  "mappings": {
    "dynamic": true,
    "fields": {
      "embedding": {
        "type": "knnVector",
        "dimensions": 1536,
        "similarity": "cosine"
      }
    }
  }
}
```

### Text Search Index (`text_index`)

```json
{
  "mappings": {
    "dynamic": true,
    "fields": {
      "content": {
        "type": "string",
        "analyzer": "lucene.standard"
      }
    }
  }
}
```

---

## Appendix B: Model Pricing Reference

| Model | Input (per 1M) | Output (per 1M) |
|-------|----------------|-----------------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-4-turbo | $10.00 | $30.00 |
| gpt-3.5-turbo | $0.50 | $1.50 |
| gemini-2.0-flash | Free tier | Free tier |
| claude-3-sonnet | $3.00 | $15.00 |
| ollama (local) | Free | Free |

---

## Appendix C: Error Codes

| Code | Description |
|------|-------------|
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Authentication required |
| 403 | Forbidden - Insufficient permissions |
| 404 | Not Found - Resource doesn't exist |
| 409 | Conflict - Resource already exists |
| 422 | Validation Error - Input validation failed |
| 500 | Internal Error - Server-side error |
| 504 | Gateway Timeout - Request timeout (30s default) |

---

*Documentation generated for RecallHub v1.0.0*
