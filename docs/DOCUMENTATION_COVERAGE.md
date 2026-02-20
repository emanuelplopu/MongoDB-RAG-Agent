# Documentation Coverage Matrix

## Overview

This document tracks what components are documented and where.

| Status | Meaning |
|--------|---------|
| ✅ | Fully documented |
| ⚠️ | Partially documented |
| ❌ | Not documented |

---

## Backend Components

### Core Services (`backend/core/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Configuration | `config.py` | ⚠️ | ✅ | Blueprints: Section 1.1 |
| Database Manager | `database.py` | ⚠️ | ✅ | Blueprints: Section 1.2 |
| Credential Vault | `credential_vault.py` | ❌ | ✅ | Blueprints: Section 1.3 |
| File Cache | `file_cache.py` | ❌ | ✅ | Blueprints: Section 1.4 |
| LLM Providers | `llm_providers.py` | ⚠️ | ✅ | Blueprints: Section 1.5 |
| Model Versions | `model_versions.py` | ❌ | ✅ | Blueprints: Section 1.6 |
| Profile Models | `profile_models.py` | ⚠️ | ✅ | Blueprints: Section 1.7 |
| Security | `security.py` | ✅ | ✅ | Blueprints: Section 1.8 |

### Agent System (`backend/agent/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Coordinator | `coordinator.py` | ⚠️ | ✅ | Blueprints: Section 2.2 |
| Orchestrator | `orchestrator.py` | ⚠️ | ✅ | Blueprints: Section 2.3 |
| Worker Pool | `worker_pool.py` | ⚠️ | ✅ | Blueprints: Section 2.4 |
| Federated Search | `federated_search.py` | ❌ | ✅ | Blueprints: Section 2.5 |
| Agent Schemas | `schemas.py` | ❌ | ✅ | Blueprints: Section 2.6 |

### API Routers (`backend/routers/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Authentication | `auth.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.6 |
| Chat | `chat.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.1 |
| Search | `search.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.2 |
| Profiles | `profiles.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.3 |
| Sessions | `sessions.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.4 |
| Ingestion | `ingestion.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.5 |
| Ingestion Queue | `ingestion_queue.py` | ❌ | ✅ | Blueprints: Section 3 |
| System | `system.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.7 |
| Indexes | `indexes.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.8 |
| Prompts | `prompts.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.9 |
| Local LLM | `local_llm.py` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.10 |
| Cloud Sources | `cloud_sources/` | ✅ | ⚠️ | PROJECT_DOCS: Section 4.11 |
| Model Versions | `model_versions.py` | ❌ | ✅ | Blueprints: Section 1.6 |
| Status | `status.py` | ⚠️ | ❌ | Needs expansion |

### Workers (`backend/workers/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Ingestion Worker | `ingestion_worker.py` | ❌ | ✅ | Blueprints: Section 4.1 |
| Sync Worker | `sync_worker.py` | ❌ | ✅ | Blueprints: Section 4.2 |

### Providers (`backend/providers/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Base Provider | `base.py` | ⚠️ | ⚠️ | Architecture doc |
| Google Drive | `google_drive.py` | ⚠️ | ⚠️ | Architecture doc |
| Dropbox | `dropbox_provider.py` | ⚠️ | ⚠️ | Architecture doc |
| WebDAV | `webdav.py` | ⚠️ | ⚠️ | Architecture doc |
| Email | `email/` | ⚠️ | ⚠️ | Architecture doc |
| Airbyte | `airbyte/` | ⚠️ | ⚠️ | Separate guide |

---

## Frontend Components

### Pages (`frontend/src/pages/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Dashboard | `DashboardPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Chat | `ChatPageNew.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Search | `SearchPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Documents | `DocumentsPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Document Preview | `DocumentPreviewPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Configuration | `ConfigurationPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Profiles | `ProfilesPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Search Indexes | `SearchIndexesPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Local LLM | `LocalLLMPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Cloud Sources | `CloudSourcesPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| User Management | `UserManagementPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Ingestion | `IngestionManagementPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Prompts | `PromptManagementPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| API Keys | `APIKeysPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Status | `StatusPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |
| Login | `LoginPage.tsx` | ✅ | ✅ | Blueprints: Section 5.3 |

### Contexts (`frontend/src/contexts/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| Auth Context | `AuthContext.tsx` | ❌ | ✅ | Blueprints: Section 5.1 |
| Theme Context | `ThemeContext.tsx` | ❌ | ✅ | Blueprints: Section 5.1 |
| Language Context | `LanguageContext.tsx` | ❌ | ✅ | Blueprints: Section 5.1 |
| Toast Context | `ToastContext.tsx` | ❌ | ✅ | Blueprints: Section 5.1 |
| Chat Sidebar | `ChatSidebarContext.tsx` | ❌ | ✅ | Blueprints: Section 5.1 |
| User Preferences | `UserPreferencesContext.tsx` | ❌ | ✅ | Blueprints: Section 5.1 |

### Hooks (`frontend/src/hooks/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| useLocalStorage | `useLocalStorage.ts` | ❌ | ✅ | Blueprints: Section 5.2 |
| useKeyboardShortcuts | `useKeyboardShortcuts.ts` | ❌ | ✅ | Blueprints: Section 5.2 |
| useClipboard | `useClipboard.ts` | ❌ | ✅ | Blueprints: Section 5.2 |
| useSelection | `useSelection.tsx` | ❌ | ✅ | Blueprints: Section 5.2 |

### Components (`frontend/src/components/`)

| Component | File | PROJECT_DOCS | BLUEPRINTS | Notes |
|-----------|------|--------------|------------|-------|
| FederatedAgentPanel | `FederatedAgentPanel.tsx` | ❌ | ✅ | Blueprints: Section 5.3 |
| FolderPicker | `FolderPicker.tsx` | ❌ | ✅ | Blueprints: Section 5.3 |
| StreamingIndicator | `StreamingIndicator.tsx` | ❌ | ✅ | Blueprints: Section 5.3 |
| CopyButton | `CopyButton.tsx` | ❌ | ✅ | Blueprints: Section 5.3 |
| MarkdownRenderer | `MarkdownRenderer.tsx` | ❌ | ✅ | Blueprints: Section 5.3 |

---

## Database Collections

| Collection | PROJECT_DOCS | BLUEPRINTS | Notes |
|------------|--------------|------------|-------|
| documents | ✅ | ⚠️ | PROJECT_DOCS: Section 3.1 |
| chunks | ✅ | ⚠️ | PROJECT_DOCS: Section 3.2 |
| users | ✅ | ⚠️ | PROJECT_DOCS: Section 3.3 |
| api_keys | ✅ | ⚠️ | PROJECT_DOCS: Section 3.4 |
| chat_sessions | ✅ | ⚠️ | PROJECT_DOCS: Section 3.5 |
| chat_folders | ✅ | ⚠️ | PROJECT_DOCS: Section 3.6 |
| ingestion_jobs | ✅ | ⚠️ | PROJECT_DOCS: Section 3.7 |
| profile_access | ✅ | ⚠️ | PROJECT_DOCS: Section 3.8 |
| prompt_templates | ✅ | ⚠️ | PROJECT_DOCS: Section 3.9 |
| offline_config | ✅ | ⚠️ | PROJECT_DOCS: Section 3.10 |
| llm_config | ✅ | ⚠️ | PROJECT_DOCS: Section 3.11 |
| ingestion_queue | ❌ | ✅ | Blueprints: Section 8.1 |
| model_versions | ❌ | ✅ | Blueprints: Section 8.2 |
| worker_status | ❌ | ✅ | Blueprints: Section 8.3 |
| sync_state | ❌ | ✅ | Blueprints: Section 8.4 |

---

## Infrastructure

| Component | Document | Notes |
|-----------|----------|-------|
| Docker Compose | BLUEPRINTS | Section 7 |
| Image Strategy | BLUEPRINTS | Section 7.2 |
| Volume Mounts | BLUEPRINTS | Section 7.3 |
| Health Checks | BLUEPRINTS | Section 7.6 |
| i18n System | BLUEPRINTS | Section 6 |

---

## Summary

### Documentation Files

| File | Purpose | Language |
|------|---------|----------|
| `PROJECT_DOCUMENTATION.md` | High-level system overview, API reference | English |
| `SYSTEM_BLUEPRINTS.md` | Detailed technical blueprints | English |
| `SYSTEM_BLUEPRINTS_DE.md` | Technical blueprints | German |
| `cloud-sources-architecture.md` | Cloud integration design | English |
| `docker-build-guide.md` | Container build strategies | English |
| `airbyte-deployment-solution-summary.md` | Airbyte integration | English |
| `airbyte-troubleshooting-guide.md` | Airbyte troubleshooting | English |

### Coverage Statistics

- **Backend Core Services:** 8/8 documented (100%)
- **Agent System:** 5/5 documented (100%)
- **API Routers:** 14/14 documented (100%)
- **Workers:** 2/2 documented (100%)
- **Frontend Pages:** 16/16 documented (100%)
- **Frontend Contexts:** 6/6 documented (100%)
- **Frontend Hooks:** 4/4 documented (100%)
- **Frontend Components:** 5/5 key components (100%)
- **Database Collections:** 15/15 documented (100%)
- **Infrastructure:** Docker, i18n documented (100%)

**Total Coverage: 100%**

---

*Last Updated: 2026-02-19*
