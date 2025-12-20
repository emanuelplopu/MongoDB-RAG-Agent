# Cloud Data Sources Integration Architecture

## Overview

This document outlines the architecture for integrating multiple cloud data sources (Google Drive, OneDrive, Dropbox, Email, OwnCloud, Confluence, Jira, etc.) into RecallHub.

## Core Design Principles

1. **Per-User Connections**: Each user manages their own OAuth tokens and credentials
2. **Unified Abstraction**: Single interface for all data sources
3. **Incremental Sync**: Efficient delta synchronization using change detection
4. **Secure Credential Storage**: Encrypted at rest with per-user isolation
5. **Horizontal Scalability**: Queue-based sync jobs with worker pools

---

## Database Schema Design

### Collections Structure

```
mongodb
├── data_source_providers           # Provider definitions (Google, Microsoft, etc.)
├── user_connections                # Per-user OAuth tokens/credentials (encrypted)
├── sync_configurations             # Folder selections, filters, schedules
├── sync_jobs                       # Job queue and history
├── sync_state                      # Delta tokens, last sync cursors
└── source_documents                # Extends existing documents collection
```

### user_connections Schema

```json
{
  "_id": "ObjectId",
  "user_id": "ObjectId (ref: users)",
  "provider": "google_drive | onedrive | dropbox | owncloud | confluence | jira | email",
  "display_name": "My Work Google Drive",
  "credentials": {
    "encrypted_data": "AES-256 encrypted blob",
    "encryption_key_id": "vault reference"
  },
  "auth_type": "oauth2 | password | api_key | certificate",
  "oauth_metadata": {
    "access_token_expires_at": "ISODate",
    "refresh_token_encrypted": "...",
    "scopes": ["drive.readonly", "..."]
  },
  "status": "active | expired | revoked | error",
  "last_validated_at": "ISODate",
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

### sync_configurations Schema

```json
{
  "_id": "ObjectId",
  "user_id": "ObjectId",
  "connection_id": "ObjectId (ref: user_connections)",
  "profile_key": "string (RAG profile to index into)",
  "name": "Project Documents",
  "source_paths": [
    {
      "path": "/Shared Drives/Engineering",
      "remote_id": "drive_folder_id",
      "include_subfolders": true
    }
  ],
  "filters": {
    "file_types": ["pdf", "docx", "md"],
    "exclude_patterns": ["**/node_modules/**", "**/.git/**"],
    "max_file_size_mb": 100,
    "modified_after": "ISODate"
  },
  "schedule": {
    "enabled": true,
    "frequency": "hourly | daily | weekly",
    "cron_expression": "0 */6 * * *",
    "next_run_at": "ISODate"
  },
  "sync_options": {
    "delete_removed": true,
    "process_updates": true
  },
  "status": "active | paused | error",
  "stats": {
    "total_files": 1250,
    "total_size_bytes": 5368709120,
    "last_sync_at": "ISODate",
    "last_sync_files_processed": 45,
    "last_sync_duration_seconds": 320
  },
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

### sync_jobs Schema

```json
{
  "_id": "ObjectId",
  "config_id": "ObjectId (ref: sync_configurations)",
  "user_id": "ObjectId",
  "connection_id": "ObjectId",
  "type": "full | incremental | manual",
  "status": "pending | running | completed | failed | cancelled",
  "started_at": "ISODate",
  "completed_at": "ISODate",
  "progress": {
    "phase": "listing | downloading | processing | indexing",
    "current_file": "path/to/file.pdf",
    "files_discovered": 1250,
    "files_processed": 834,
    "files_skipped": 200,
    "files_failed": 5,
    "bytes_processed": 2684354560
  },
  "errors": [
    {
      "file_path": "...",
      "error_type": "access_denied | rate_limited | parse_error",
      "message": "...",
      "timestamp": "ISODate"
    }
  ],
  "delta_token": "for incremental sync state"
}
```

---

## Provider Implementations

### Provider Interface (Python)

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional
from dataclasses import dataclass

@dataclass
class RemoteFile:
    id: str
    name: str
    path: str
    mime_type: str
    size_bytes: int
    modified_at: datetime
    checksum: Optional[str] = None
    download_url: Optional[str] = None

@dataclass
class SyncDelta:
    added: list[RemoteFile]
    modified: list[RemoteFile]
    deleted: list[str]  # file IDs
    next_delta_token: str

class CloudSourceProvider(ABC):
    """Base interface for all cloud storage providers."""
    
    @abstractmethod
    async def authenticate(self, credentials: dict) -> dict:
        """Validate and potentially refresh credentials."""
        pass
    
    @abstractmethod
    async def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Generate OAuth authorization URL."""
        pass
    
    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange OAuth code for tokens."""
        pass
    
    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> dict:
        """Refresh expired access token."""
        pass
    
    @abstractmethod
    async def list_folders(self, path: str = "/") -> list[dict]:
        """List folders for selection UI."""
        pass
    
    @abstractmethod
    async def list_files(
        self, 
        folder_id: str,
        recursive: bool = True
    ) -> AsyncIterator[RemoteFile]:
        """Stream file listing."""
        pass
    
    @abstractmethod
    async def get_changes(
        self, 
        delta_token: Optional[str] = None
    ) -> SyncDelta:
        """Get changes since last sync (incremental)."""
        pass
    
    @abstractmethod
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Stream file content."""
        pass
    
    @abstractmethod
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get single file metadata."""
        pass
```

### Provider Implementations

| Provider | Auth Type | Delta Sync | Notes |
|----------|-----------|------------|-------|
| Google Drive | OAuth 2.0 | Changes API | Excellent delta support |
| OneDrive | OAuth 2.0 | Delta API | Microsoft Graph API |
| Dropbox | OAuth 2.0 | list_folder/continue | Cursor-based |
| OwnCloud | Password/App Token | WebDAV PROPFIND | No native delta |
| Confluence | OAuth 2.0 / API Token | CQL queries | Atlassian Cloud |
| Jira | OAuth 2.0 / API Token | JQL queries | Issue attachments |
| IMAP Email | Password/OAuth | UIDVALIDITY | Standard protocol |
| SharePoint | OAuth 2.0 | Delta API | Same as OneDrive |

---

## Backend API Design

### Router Structure

```
backend/routers/
├── cloud_sources/
│   ├── __init__.py
│   ├── providers.py      # Provider definitions
│   ├── connections.py    # User connection management
│   ├── sync.py           # Sync configuration & execution
│   ├── oauth.py          # OAuth flow handlers
│   └── schemas.py        # Pydantic models
```

### API Endpoints

```
# Provider Discovery
GET  /api/v1/cloud-sources/providers
     → List available providers with capabilities

# OAuth Flow
GET  /api/v1/cloud-sources/oauth/{provider}/authorize
     → Redirect to provider's OAuth consent screen
GET  /api/v1/cloud-sources/oauth/{provider}/callback
     → Handle OAuth callback, store tokens
POST /api/v1/cloud-sources/oauth/{provider}/refresh
     → Manually refresh tokens

# Connection Management
GET  /api/v1/cloud-sources/connections
     → List user's connections
POST /api/v1/cloud-sources/connections
     → Create new connection (password/API key types)
GET  /api/v1/cloud-sources/connections/{id}
     → Get connection details
DELETE /api/v1/cloud-sources/connections/{id}
     → Revoke and delete connection
POST /api/v1/cloud-sources/connections/{id}/test
     → Test connection validity

# Folder Browser (for UI selection)
GET  /api/v1/cloud-sources/connections/{id}/browse
     ?path=/folder/path
     → List folders and files at path

# Sync Configurations
GET  /api/v1/cloud-sources/sync-configs
     → List user's sync configurations
POST /api/v1/cloud-sources/sync-configs
     → Create sync configuration
PUT  /api/v1/cloud-sources/sync-configs/{id}
     → Update sync configuration
DELETE /api/v1/cloud-sources/sync-configs/{id}
     → Delete sync configuration

# Sync Operations
POST /api/v1/cloud-sources/sync-configs/{id}/run
     → Trigger manual sync
GET  /api/v1/cloud-sources/sync-configs/{id}/status
     → Get current sync status
POST /api/v1/cloud-sources/sync-configs/{id}/pause
POST /api/v1/cloud-sources/sync-configs/{id}/resume
POST /api/v1/cloud-sources/sync-configs/{id}/cancel

# Sync History
GET  /api/v1/cloud-sources/sync-configs/{id}/history
     → Paginated sync job history
GET  /api/v1/cloud-sources/jobs/{job_id}
     → Detailed job status with file list
GET  /api/v1/cloud-sources/jobs/{job_id}/logs
     → Stream job logs (SSE)

# Dashboard Aggregates
GET  /api/v1/cloud-sources/dashboard
     → Summary stats for all user's sources
```

---

## Frontend Components

### Page Structure

```
frontend/src/pages/
├── CloudSourcesPage.tsx          # Main dashboard
├── CloudSourceConnectionsPage.tsx # Manage connections
├── CloudSourceSyncPage.tsx       # Configure sync jobs
└── CloudSourceBrowserPage.tsx    # Folder picker modal

frontend/src/components/cloud-sources/
├── ProviderCard.tsx              # Provider selection card
├── OAuthButton.tsx               # OAuth connect button
├── FolderPicker.tsx              # Tree-based folder selection
├── SyncConfigForm.tsx            # Sync settings form
├── SyncStatusCard.tsx            # Real-time sync progress
├── ConnectionStatusBadge.tsx     # Connection health indicator
└── SyncHistoryTable.tsx          # Past sync runs
```

### Main Dashboard (CloudSourcesPage.tsx)

```tsx
// Key sections:
// 1. Overview Cards - Total sources, files indexed, last sync
// 2. Quick Actions - Add new source, run all syncs
// 3. Sources Grid - Each connected source with status
// 4. Recent Activity - Latest sync jobs timeline

interface DashboardStats {
  total_connections: number;
  active_syncs: number;
  total_files_indexed: number;
  total_size_gb: number;
  next_scheduled_sync: string;
}

interface SourceSummary {
  id: string;
  provider: string;
  display_name: string;
  status: 'healthy' | 'warning' | 'error';
  files_count: number;
  last_sync: string;
  next_sync?: string;
}
```

### Folder Picker Component

```tsx
interface FolderPickerProps {
  connectionId: string;
  selectedPaths: string[];
  onSelect: (paths: SelectedPath[]) => void;
}

interface SelectedPath {
  path: string;
  remote_id: string;
  include_subfolders: boolean;
}

// Features:
// - Lazy-load tree expansion
// - Multi-select with checkboxes
// - Search/filter
// - Preview file count per folder
// - Breadcrumb navigation
```

---

## Sync Worker Architecture

### Worker Pool Design

```python
# backend/workers/sync_worker.py

class SyncWorkerPool:
    """Manages concurrent sync job execution."""
    
    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.active_jobs: dict[str, SyncJob] = {}
        self.job_queue: asyncio.Queue = asyncio.Queue()
    
    async def start(self):
        """Start worker pool."""
        for i in range(self.max_workers):
            asyncio.create_task(self._worker(i))
    
    async def _worker(self, worker_id: int):
        """Individual worker loop."""
        while True:
            job = await self.job_queue.get()
            try:
                await self._execute_sync(job)
            except Exception as e:
                await self._handle_failure(job, e)
            finally:
                self.job_queue.task_done()

class SyncJobExecutor:
    """Executes a single sync job."""
    
    async def execute(self, job: SyncJob):
        # 1. Load credentials
        credentials = await self._load_credentials(job.connection_id)
        
        # 2. Initialize provider
        provider = get_provider(job.provider_type)
        await provider.authenticate(credentials)
        
        # 3. Get changes (incremental) or full listing
        if job.type == 'incremental':
            delta = await provider.get_changes(job.last_delta_token)
            files_to_process = delta.added + delta.modified
            files_to_delete = delta.deleted
        else:
            files_to_process = await self._list_all_files(provider, job.config)
            files_to_delete = []
        
        # 4. Process each file
        for file in files_to_process:
            if self._should_process(file, job.config.filters):
                content = await provider.download_file(file.id)
                await self._ingest_file(file, content, job)
        
        # 5. Handle deletions
        for file_id in files_to_delete:
            await self._remove_from_index(file_id, job)
        
        # 6. Update sync state
        await self._save_delta_token(job, delta.next_delta_token)
```

### Scheduler Integration

```python
# backend/workers/sync_scheduler.py

class SyncScheduler:
    """Schedules periodic sync jobs."""
    
    async def start(self):
        """Start scheduler loop."""
        while True:
            # Find due sync configurations
            due_configs = await self._get_due_sync_configs()
            
            for config in due_configs:
                # Create job and add to queue
                job = await self._create_sync_job(config, type='incremental')
                await self.worker_pool.submit(job)
                
                # Update next run time
                await self._update_next_run(config)
            
            await asyncio.sleep(60)  # Check every minute
```

---

## Security Considerations

### Credential Storage

```python
# Use age or similar for encryption
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

class CredentialVault:
    """Secure credential storage with encryption at rest."""
    
    def __init__(self, master_key: bytes):
        self.cipher = Fernet(master_key)
    
    def encrypt_credentials(self, credentials: dict) -> bytes:
        """Encrypt credentials before storage."""
        data = json.dumps(credentials).encode()
        return self.cipher.encrypt(data)
    
    def decrypt_credentials(self, encrypted: bytes) -> dict:
        """Decrypt credentials for use."""
        data = self.cipher.decrypt(encrypted)
        return json.loads(data.decode())
```

### OAuth Security

1. **State Parameter**: Prevent CSRF with cryptographic state tokens
2. **PKCE**: Use Proof Key for Code Exchange for public clients
3. **Scope Minimization**: Request only necessary permissions
4. **Token Rotation**: Refresh tokens before expiration
5. **Revocation**: Provide clear disconnect/revoke functionality

---

## Implementation Phases

### Phase 1: Foundation (2-3 weeks)
- [ ] Database schema and migrations
- [ ] Provider interface and base classes
- [ ] Credential vault implementation
- [ ] Basic API structure

### Phase 2: Core Providers (3-4 weeks)
- [ ] Google Drive implementation
- [ ] OneDrive/SharePoint implementation
- [ ] Dropbox implementation
- [ ] OAuth flow handlers

### Phase 3: Enterprise Sources (2-3 weeks)
- [ ] OwnCloud/NextCloud (WebDAV)
- [ ] Confluence implementation
- [ ] Jira implementation
- [ ] IMAP email implementation

### Phase 4: Sync Engine (2-3 weeks)
- [ ] Worker pool implementation
- [ ] Incremental sync with delta tokens
- [ ] Scheduler integration
- [ ] Progress tracking and SSE

### Phase 5: Frontend (3-4 weeks)
- [ ] Dashboard page
- [ ] Connection management
- [ ] Folder picker component
- [ ] Sync configuration UI
- [ ] Real-time status updates

### Phase 6: Polish (1-2 weeks)
- [ ] Error handling and retry logic
- [ ] Rate limiting compliance
- [ ] Monitoring and alerts
- [ ] Documentation

---

## Airbyte Integration (Optional)

For complex sources or when maintenance is a concern:

```yaml
# docker-compose.airbyte.yml
services:
  airbyte-server:
    image: airbyte/server:latest
    ports:
      - "8001:8001"
    volumes:
      - airbyte_data:/data
    environment:
      - DATABASE_URL=postgresql://...
  
  airbyte-worker:
    image: airbyte/worker:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

Integration approach:
1. Use Airbyte API to manage connections programmatically
2. Configure destinations to write to MongoDB
3. Trigger syncs via Airbyte API from your application
4. Monitor sync status through Airbyte webhooks

---

## File Structure Summary

```
backend/
├── core/
│   └── credential_vault.py
├── routers/
│   └── cloud_sources/
│       ├── __init__.py
│       ├── connections.py
│       ├── oauth.py
│       ├── providers.py
│       ├── schemas.py
│       └── sync.py
├── providers/
│   ├── __init__.py
│   ├── base.py
│   ├── google_drive.py
│   ├── onedrive.py
│   ├── dropbox.py
│   ├── owncloud.py
│   ├── confluence.py
│   ├── jira.py
│   └── email_imap.py
└── workers/
    ├── __init__.py
    ├── sync_worker.py
    └── sync_scheduler.py

frontend/src/
├── pages/
│   ├── CloudSourcesPage.tsx
│   ├── CloudSourceConnectionsPage.tsx
│   └── CloudSourceSyncPage.tsx
└── components/
    └── cloud-sources/
        ├── ProviderCard.tsx
        ├── OAuthButton.tsx
        ├── FolderPicker.tsx
        ├── SyncConfigForm.tsx
        ├── SyncStatusCard.tsx
        └── SyncHistoryTable.tsx
```
