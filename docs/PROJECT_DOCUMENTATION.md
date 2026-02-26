# RecallHub - Complete Project Documentation

> **Related Documentation:**
> - [System Blueprints (EN)](./SYSTEM_BLUEPRINTS.md) - Detailed technical blueprints for all system components
> - [Systemblaupausen (DE)](./SYSTEM_BLUEPRINTS_DE.md) - Deutsche Version der technischen Blaupausen
> - [Cloud Sources Architecture](./architecture/cloud-sources-architecture.md) - Cloud integration design
> - [Docker Build Guide](./docker-build-guide.md) - Container build strategies
> - [Airbyte Deployment](./airbyte-deployment-solution-summary.md) - Airbyte integration guide

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
10. [Backup & Restore System](#10-backup--restore-system)
11. [Embedding Benchmark System](#11-embedding-benchmark-system)
12. [Frontend Pages](#12-frontend-pages)
13. [Environment Variables](#13-environment-variables)

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

#### 3.12 `file_registry` Collection
File classification and selective ingestion tracking.

```javascript
{
  "_id": ObjectId,
  "file_path": String,              // Full file path
  "profile_key": String,            // Associated profile
  "classification": String,         // pending/completed/failed/timeout/image_only_pdf/no_chunks/excluded
  "file_hash": String,              // SHA-256 hash for change detection
  "file_size": Number,
  "last_processed_at": ISODate,
  "error_message": String,          // Error details if failed
  "chunks_created": Number,
  "processing_time_ms": Number
}
```

#### 3.13 `ingestion_schedules` Collection
Scheduled ingestion job configurations.

```javascript
{
  "_id": String,                    // Schedule UUID
  "profile_key": String,
  "profile_name": String,
  "file_types": [String],           // ["all", "documents", "images", "audio", "video"]
  "incremental": Boolean,
  "frequency": String,              // hourly/daily/weekly/monthly
  "hour": Number,                   // 0-23 for daily/weekly/monthly
  "day_of_week": Number,            // 0-6 for weekly
  "day_of_month": Number,           // 1-31 for monthly
  "enabled": Boolean,
  "last_run": ISODate,
  "next_run": ISODate,
  "created_at": ISODate,
  // Selective ingestion filters
  "retry_image_only_pdfs": Boolean,
  "retry_timeouts": Boolean,
  "retry_errors": Boolean,
  "retry_no_chunks": Boolean,
  "skip_image_only_pdfs": Boolean
}
```

#### 3.14 `backups` Collection
Backup metadata and restore tracking.

```javascript
{
  "_id": String,                    // Backup UUID
  "backup_type": String,            // full/incremental/checkpoint/post_ingestion
  "profile_key": String,
  "name": String,
  "description": String,
  "status": String,                 // pending/running/completed/failed
  "file_path": String,              // Path to backup file
  "file_size_bytes": Number,
  "created_at": ISODate,
  "completed_at": ISODate,
  "parent_backup_id": String,       // For incremental backups
  "collections": [{
    "name": String,
    "document_count": Number,
    "size_bytes": Number
  }],
  "include_embeddings": Boolean,
  "compressed": Boolean,
  "checksum": String                // SHA-256 for integrity
}
```

#### 3.15 `benchmark_results` Collection
Embedding benchmark results.

```javascript
{
  "_id": String,                    // Benchmark UUID
  "timestamp": ISODate,
  "file_name": String,
  "file_size_bytes": Number,
  "content_preview": String,
  "chunk_config": {
    "chunk_size": Number,
    "chunk_overlap": Number,
    "max_tokens": Number
  },
  "results": [{
    "provider": String,
    "model": String,
    "provider_type": String,
    "total_time_ms": Number,
    "embedding_time_ms": Number,
    "avg_latency_ms": Number,
    "tokens_processed": Number,
    "chunks_created": Number,
    "embedding_dimension": Number,
    "memory_peak_mb": Number,
    "cost_estimate_usd": Number,
    "success": Boolean,
    "error": String
  }],
  "winner": String
}
```

#### 3.16 `strategy_metrics` Collection
Agent strategy execution metrics for A/B testing.

```javascript
{
  "_id": ObjectId,
  "strategy_id": String,
  "session_id": String,
  "query": String,
  "domain": String,
  "execution_time_ms": Number,
  "iterations": Number,
  "confidence_score": Number,
  "quality_score": Number,          // 0-100
  "sources_retrieved": Number,
  "user_feedback_score": Number,    // 1-5 if provided
  "user_feedback_text": String,
  "timestamp": ISODate
}
```

#### 3.17 `backup_config` Collection
Backup system configuration.

```javascript
{
  "_id": "backup_config",
  "auto_backup_after_ingestion": Boolean,
  "retention_days": Number,
  "max_backups_per_profile": Number,
  "compression_enabled": Boolean,
  "include_embeddings": Boolean,
  "backup_schedule": String,        // Cron expression
  "backup_directory": String
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

### 4.11 Status Dashboard (`/api/v1/status`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard` | Comprehensive status dashboard with KPIs |
| GET | `/metrics/profile/{key}` | Detailed metrics for a specific profile |
| GET | `/health/detailed` | Component-level health check |

### 4.12 Ingestion Queue (`/api/v1/ingestion-queue`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/queue` | Get current queue status |
| POST | `/queue/add` | Add job to queue |
| POST | `/queue/add-multiple` | Add multiple jobs to queue |
| DELETE | `/queue/{id}` | Remove job from queue |
| DELETE | `/queue` | Clear all queued jobs |
| POST | `/queue/reorder` | Reorder queue by job IDs |
| GET | `/schedules` | List scheduled jobs |
| POST | `/schedules` | Create scheduled job |
| PUT | `/schedules/{id}` | Update scheduled job |
| DELETE | `/schedules/{id}` | Delete scheduled job |
| POST | `/schedules/{id}/toggle` | Enable/disable schedule |
| POST | `/schedules/{id}/run-now` | Trigger schedule immediately |

### 4.13 File Registry (`/api/v1/file-registry`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stats` | File registry statistics by classification |
| GET | `/files` | List files with filters |
| POST | `/reclassify/{path}` | Manually reclassify a file |
| DELETE | `/clear` | Clear registry entries |
| POST | `/retry-category` | Mark files for retry by category |

### 4.14 Model Versions (`/api/v1/model-versions`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List all available models |
| GET | `/latest` | Get latest released models |
| GET | `/cost-effective` | Get most cost-effective models |
| GET | `/{model_id}` | Get model details |
| POST | `/switch` | Switch model versions |
| POST | `/check-compatibility` | Check model parameter compatibility |
| GET | `/recommendations` | Get model recommendations |
| GET | `/current` | Get currently configured models |

### 4.15 Agent Strategies (`/api/v1/strategies`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List available strategies |
| GET | `/default` | Get default strategy |
| GET | `/{strategy_id}` | Get strategy details |
| GET | `/{strategy_id}/metrics` | Get strategy performance metrics |
| POST | `/compare` | Compare two strategies |
| GET | `/for-domain/{domain}` | Get best strategy for domain |
| POST | `/auto-detect` | Auto-detect strategy from query |
| GET | `/metrics/all` | Get metrics for all strategies |
| POST | `/feedback` | Record user feedback |
| POST | `/ab-compare-responses` | LLM-based A/B response comparison |

### 4.16 Backup & Restore (`/api/v1/backups`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/create` | Create backup (full/incremental/checkpoint) |
| POST | `/checkpoint` | Create lightweight checkpoint |
| GET | `/` | List all backups |
| GET | `/checkpoints` | List checkpoints only |
| GET | `/{backup_id}` | Get backup details |
| GET | `/{backup_id}/chain` | Get incremental backup chain |
| POST | `/{backup_id}/restore` | Restore from backup |
| DELETE | `/{backup_id}` | Delete backup |
| GET | `/config` | Get backup configuration |
| PUT | `/config` | Update backup configuration |
| GET | `/status` | Get current backup operation status |
| GET | `/storage` | Get backup storage statistics |

### 4.17 Embedding Benchmark (`/api/v1/benchmark`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/run` | Run embedding benchmark |
| POST | `/run-file` | Run benchmark with file upload |
| GET | `/providers` | Get available embedding providers |
| POST | `/test-provider` | Test provider connectivity |
| GET | `/results` | Get historical benchmark results |
| GET | `/results/{id}` | Get specific benchmark result |
| DELETE | `/results/{id}` | Delete benchmark result |

### 4.18 Cloud Sources Cache (`/api/v1/cloud-sources/cache`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/get-file` | Get or cache cloud file |
| GET | `/serve/{conn_id}/{doc_id}` | Serve cached file for preview |
| GET | `/stats/{connection_id}` | Get cache statistics |
| DELETE | `/clear/{connection_id}` | Clear cache for connection |
| GET | `/info/{document_id}` | Get cloud source info for document |

### 4.19 Cloud Sources (`/api/v1/cloud-sources`)

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

### 7.5 Agent Strategies

Strategies define how the orchestrator behaves for different use cases.

#### Strategy Domains

| Domain | Description |
|--------|-------------|
| `general` | General-purpose knowledge retrieval |
| `software_dev` | Software development queries |
| `legal` | Legal document analysis |
| `hr` | Human resources queries |

#### Strategy Configuration

```python
@dataclass
class StrategyConfig:
    max_iterations: int          # Maximum orchestrator loops
    confidence_threshold: float   # Early exit threshold (0.0-1.0)
    early_exit_enabled: bool      # Allow early termination
    cross_search_boost: float     # Boost for cross-profile results
    content_length_penalty: float # Penalty for verbose responses
    custom_params: dict           # Strategy-specific parameters
```

#### Strategy Prompts

Each strategy defines custom prompts for:
- **Analyze**: Query understanding and intent extraction
- **Plan**: Task creation and resource allocation
- **Evaluate**: Result quality assessment
- **Synthesize**: Final response generation

#### A/B Testing

Strategies support A/B testing with:
- Execution metrics (latency, iterations, confidence)
- Quality scoring (0-100 based on response quality)
- User feedback collection (1-5 rating)
- LLM-based response comparison

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

### 8.5 Selective Ingestion

The File Registry tracks file processing status for selective re-ingestion.

#### File Classifications

| Classification | Description |
|----------------|-------------|
| `pending` | Not yet processed |
| `completed` | Successfully processed |
| `failed` | Processing failed with error |
| `timeout` | Processing timed out |
| `image_only_pdf` | PDF with only images (OCR needed) |
| `no_chunks` | Processed but no chunks created |
| `excluded` | Explicitly excluded from processing |

#### Selective Ingestion Filters

| Filter | Description |
|--------|-------------|
| `retry_image_only_pdfs` | Reprocess image-only PDFs |
| `retry_timeouts` | Retry files that timed out |
| `retry_errors` | Retry previously failed files |
| `retry_no_chunks` | Retry files that created no chunks |
| `skip_image_only_pdfs` | Skip image-only PDFs entirely |

### 8.6 Ingestion Queue

Advanced queue management for batch processing.

#### Queue Features

- **Priority Queue**: Higher priority jobs execute first
- **Multi-Profile**: Queue jobs for different profiles
- **File Type Filtering**: Process specific file types only
- **Background Processing**: Non-blocking queue execution

#### Job Types

| Type | Description |
|------|-------------|
| `queued` | Waiting in queue |
| `running` | Currently executing |
| `completed` | Successfully finished |
| `failed` | Failed with error |
| `cancelled` | Manually cancelled |

### 8.7 Scheduled Ingestion

Automatic ingestion based on time schedules.

#### Schedule Frequencies

| Frequency | Description |
|-----------|-------------|
| `hourly` | Run every hour |
| `daily` | Run once per day at specified hour |
| `weekly` | Run once per week on specified day |
| `monthly` | Run once per month on specified day |

#### Schedule Configuration

```yaml
schedule:
  frequency: daily
  hour: 2              # Run at 2 AM
  file_types: ["all"]
  incremental: true
  retry_errors: true   # Retry failed files
```

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

### 9.4 Cloud Source File Caching

Local caching system for cloud source documents.

#### Cache Features

- **On-Demand Download**: Files downloaded when needed for preview
- **Access Tracking**: Track access count and last accessed time
- **Size Limits**: Configurable cache size per connection
- **Auto-Cleanup**: Remove least recently used files when limit reached

#### Cache Information

| Field | Description |
|-------|-------------|
| `remote_id` | File ID in cloud provider |
| `local_path` | Local cached file path |
| `cached_at` | When file was cached |
| `last_accessed_at` | Last access timestamp |
| `access_count` | Number of times accessed |
| `web_view_url` | Link to view in cloud provider |

---

## 10. Backup & Restore System

### 10.1 Backup Types

| Type | Description | Use Case |
|------|-------------|----------|
| `full` | Complete database backup | Regular scheduled backups |
| `incremental` | Changes since last backup | Frequent backups with low storage |
| `checkpoint` | Lightweight state snapshot | Quick restore points |
| `post_ingestion` | Auto-backup after ingestion | Data protection after changes |

### 10.2 Backup Configuration

```yaml
backup_config:
  auto_backup_after_ingestion: true
  retention_days: 30
  max_backups_per_profile: 10
  compression_enabled: true
  include_embeddings: false       # Embeddings can be regenerated
  backup_schedule: "0 0 * * 0"    # Weekly on Sunday
```

### 10.3 Restore Modes

| Mode | Description |
|------|-------------|
| `full` | Replace all data with backup data |
| `merge` | Add missing documents only |
| `selective` | Restore specific collections |

### 10.4 Restore Options

| Option | Description |
|--------|-------------|
| `skip_users` | Don't restore user accounts |
| `skip_sessions` | Don't restore chat sessions |
| `collections` | Specific collections to restore |

### 10.5 Backup Chain

Incremental backups form a chain:

```
Full Backup (Base)
    └── Incremental 1
            └── Incremental 2
                    └── Incremental 3 (Latest)
```

To restore Incremental 3, all backups in the chain are needed.

---

## 11. Embedding Benchmark System

### 11.1 Overview

Compare embedding providers to find the best fit for your use case.

### 11.2 Supported Providers

| Provider | Type | Description |
|----------|------|-------------|
| OpenAI | Cloud API | text-embedding-3-small, ada-002 |
| Ollama | Local | nomic-embed-text, all-minilm |
| vLLM | Self-hosted | Custom embedding models |

### 11.3 Benchmark Metrics

| Metric | Description |
|--------|-------------|
| `total_time_ms` | End-to-end processing time |
| `chunking_time_ms` | Time for text chunking |
| `embedding_time_ms` | Time for embedding generation |
| `avg_latency_ms` | Average latency per chunk |
| `tokens_processed` | Total tokens embedded |
| `embedding_dimension` | Vector dimensions |
| `memory_peak_mb` | Peak memory usage |
| `cost_estimate_usd` | Estimated cost (for cloud APIs) |

### 11.4 Running Benchmarks

1. Upload a test document
2. Configure providers to compare (max 3)
3. Set chunking parameters
4. Run benchmark
5. Review results and select winner

---

## 12. Frontend Pages

### 12.1 Main Pages

| Page | Path | Description |
|------|------|-------------|
| Dashboard | `/` | Dashboard with quick actions and stats |
| Chat | `/chat/:id?` | Chat interface with sessions |
| Search | `/search` | Direct search interface |
| Documents | `/documents` | Browse indexed documents |
| Document Preview | `/documents/:id` | Preview document content |
| Landing | `/landing` | Public landing page |

### 12.2 Configuration Pages

| Page | Path | Description |
|------|------|-------------|
| Configuration | `/configuration` | LLM and system settings |
| Profiles | `/profiles` | Profile management |
| Search Indexes | `/indexes` | Index status and setup |
| Local LLM | `/local-llm` | Offline mode configuration |

### 12.3 Integration Pages

| Page | Path | Description |
|------|------|-------------|
| Cloud Sources | `/cloud-sources` | Cloud provider connections |
| Cloud Connections | `/cloud-sources/connections` | Manage connections |
| Cloud Connect | `/cloud-sources/connect/:type` | OAuth connection flow |
| Email Config | `/cloud-sources/email/:type` | Email source setup |

### 12.4 Admin Pages

| Page | Path | Description |
|------|------|-------------|
| User Management | `/admin/users` | User CRUD, access control |
| Ingestion | `/admin/ingestion` | Document ingestion control |
| Ingestion Analytics | `/admin/ingestion/analytics` | Ingestion metrics and charts |
| Job History | `/admin/ingestion/history` | Ingestion job history |
| Failed Documents | `/admin/ingestion/failed` | Failed document management |
| Prompts | `/admin/prompts` | Prompt template management |
| API Keys | `/api-keys` | Personal API key management |
| Status | `/status` | System status dashboard |
| Backups | `/admin/backups` | Backup and restore management |

### 12.5 Advanced Pages

| Page | Path | Description |
|------|------|-------------|
| Strategies | `/strategies` | Agent strategy management |
| Strategy A/B Test | `/strategies/ab-test` | A/B testing comparison |
| Embedding Benchmark | `/benchmark` | Embedding provider comparison |
| Developer Docs | `/developer-docs` | API documentation viewer |
| Archived Chats | `/archived` | View archived chat sessions |

---

## 13. Environment Variables

### 13.1 Required Variables

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

### 13.2 Optional Variables

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

# Backup Configuration
BACKUP_AUTO_AFTER_INGESTION=true
BACKUP_RETENTION_DAYS=30
BACKUP_DIRECTORY=./backups

# Ingestion Performance
INGESTION_MAX_CONCURRENT_FILES=1
INGESTION_CHUNK_SIZE=1000
INGESTION_CHUNK_OVERLAP=200
```

### 13.3 Docker Environment

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

*Documentation generated for RecallHub v1.1.0 - Updated with complete API coverage*
