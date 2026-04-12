# Cloud Source Integration

<cite>
**Referenced Files in This Document**
- [registry.py](file://backend/providers/registry.py)
- [base.py](file://backend/providers/base.py)
- [google_drive.py](file://backend/providers/google_drive.py)
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py)
- [webdav.py](file://backend/providers/webdav.py)
- [confluence.py](file://backend/providers/airbyte/confluence.py)
- [jira.py](file://backend/providers/airbyte/jira.py)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py)
- [base.py](file://backend/providers/airbyte/base.py)
- [providers.py](file://backend/routers/cloud_sources/providers.py)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document explains the cloud source integration capabilities of the system, focusing on the provider registry, authentication framework, OAuth flows, and provider implementations. It covers file storage providers (Google Drive, Dropbox, WebDAV/OwnCloud/Nextcloud), collaboration platforms (Confluence, Jira), and email providers (Gmail, Outlook, IMAP). It also documents the Airbyte integration pattern for enterprise cloud sources and outlines extensibility guidelines for adding custom providers that implement the CloudSourceProvider interface.

## Project Structure
The cloud source integration spans three main areas:
- Provider registry and base interfaces
- Provider implementations (file storage, WebDAV, Airbyte-backed)
- Router endpoints for provider discovery and OAuth flows

```mermaid
graph TB
subgraph "Provider Registry"
REG[registry.py]
BASE[base.py]
end
subgraph "Direct SDK Providers"
GD[google_drive.py]
DB[dropbox_provider.py]
WD[webdav.py]
end
subgraph "Airbyte Providers"
CF[airbyte/confluence.py]
JR[airbyte/jira.py]
GG[airbyte/email_gmail.py]
GO[airbyte/email_outlook.py]
IM[airbyte/email_imap.py]
AB[airbyte/base.py]
end
subgraph "Routers"
PR[cloud_sources/providers.py]
OA[cloud_sources/oauth.py]
end
REG --> GD
REG --> DB
REG --> WD
REG --> CF
REG --> JR
REG --> GG
REG --> GO
REG --> IM
GD --> BASE
DB --> BASE
WD --> BASE
CF --> AB
JR --> AB
GG --> AB
GO --> AB
IM --> AB
PR --> REG
OA --> REG
```

**Diagram sources**
- [registry.py](file://backend/providers/registry.py#L1-L143)
- [base.py](file://backend/providers/base.py#L1-L525)
- [google_drive.py](file://backend/providers/google_drive.py#L1-L627)
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py#L1-L582)
- [webdav.py](file://backend/providers/webdav.py#L1-L551)
- [confluence.py](file://backend/providers/airbyte/confluence.py#L1-L337)
- [jira.py](file://backend/providers/airbyte/jira.py#L1-L370)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L1-L367)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L1-L302)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L1-L394)
- [base.py](file://backend/providers/airbyte/base.py#L1-L566)
- [providers.py](file://backend/routers/cloud_sources/providers.py#L1-L184)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L1-L537)

**Section sources**
- [registry.py](file://backend/providers/registry.py#L1-L143)
- [base.py](file://backend/providers/base.py#L1-L525)
- [providers.py](file://backend/routers/cloud_sources/providers.py#L1-L184)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L1-L537)

## Core Components
- Provider Registry and Factory: Centralized registry that registers provider implementations and instantiates them by type. It auto-imports providers to populate the registry and exposes helper functions to list and validate providers.
- Base Provider Interface: Defines the CloudSourceProvider abstract interface, common data structures (ProviderCapabilities, ConnectionCredentials, OAuthTokens, RemoteFile, RemoteFolder, SyncDelta), and standardized methods for authentication, browsing, file operations, and sync.
- Router Endpoints: Expose provider capabilities and manage OAuth flows for supported providers.

Key responsibilities:
- Provider registration and discovery
- Unified authentication and credential model
- Standardized sync operations and delta handling
- OAuth initiation, callback, token refresh, and revocation
- Airbyte integration for enterprise sources

**Section sources**
- [registry.py](file://backend/providers/registry.py#L20-L76)
- [base.py](file://backend/providers/base.py#L179-L491)
- [providers.py](file://backend/routers/cloud_sources/providers.py#L24-L147)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L128-L146)

## Architecture Overview
The system uses a provider registry to instantiate concrete providers that implement the CloudSourceProvider interface. For enterprise sources, providers inherit from AirbyteProvider and integrate with Airbyte’s source/destination/sync pipeline. OAuth flows are managed by dedicated router endpoints that handle authorization URLs, callbacks, token exchange, and refresh.

```mermaid
sequenceDiagram
participant Client as "Client App"
participant OAuth as "OAuth Router"
participant Provider as "Provider Factory"
participant Impl as "Provider Implementation"
participant DB as "Connections DB"
Client->>OAuth : POST /api/v1/cloud-sources/oauth/{provider}/authorize
OAuth->>OAuth : Generate state<br/>Store state mapping
OAuth-->>Client : authorization_url + state
Client->>Provider : Browser redirects to provider OAuth consent
Provider-->>Client : Authorization callback with code
Client->>OAuth : GET /api/v1/cloud-sources/oauth/{provider}/callback?code&state
OAuth->>OAuth : Validate state<br/>Exchange code for tokens
OAuth->>OAuth : Fetch user info
OAuth->>DB : Insert connection document
OAuth-->>Client : Redirect success
Client->>Provider : Create provider instance
Provider->>Impl : Instantiate by type
Impl-->>Provider : Provider ready
```

**Diagram sources**
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L155-L218)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L221-L319)
- [registry.py](file://backend/providers/registry.py#L45-L66)
- [providers.py](file://backend/routers/cloud_sources/providers.py#L150-L160)

## Detailed Component Analysis

### Provider Registry and Factory
The registry centralizes provider registration and instantiation. It maintains a mapping from ProviderType to provider classes, auto-imports implementations, and exposes helpers to list and validate providers.

```mermaid
classDiagram
class ProviderRegistry {
+register_provider(provider_type)
+get_provider_class(provider_type)
+create_provider(provider_type, credentials)
+list_registered_providers()
+is_provider_available(provider_type)
-_provider_registry : dict
-_load_providers()
}
class CloudSourceProvider {
<<abstract>>
+provider_type
+capabilities
+authenticate(credentials) bool
+validate_credentials() bool
+refresh_credentials() ConnectionCredentials
+get_oauth_authorization_url(...)
+exchange_oauth_code(...)
+revoke_oauth_tokens() bool
+list_root_folders() RemoteFolder[]
+list_folder_contents(...) (RemoteFolder[], RemoteFile[])
+get_file_metadata(file_id) RemoteFile
+download_file(file_id) AsyncIterator~bytes~
+list_all_files(folder_id, recursive, file_types) AsyncIterator~RemoteFile~
+get_changes(delta_token, folder_id) SyncDelta
+subscribe_to_changes(folder_id, webhook_url) str
+unsubscribe_from_changes(subscription_id) bool
+get_storage_quota() dict
+get_user_info() dict
+close() void
}
ProviderRegistry --> CloudSourceProvider : "instantiates"
```

**Diagram sources**
- [registry.py](file://backend/providers/registry.py#L24-L76)
- [base.py](file://backend/providers/base.py#L179-L491)

**Section sources**
- [registry.py](file://backend/providers/registry.py#L20-L143)
- [base.py](file://backend/providers/base.py#L179-L491)

### Base Provider Interface and Data Models
The base module defines:
- ProviderType enumeration for supported providers
- AuthType enumeration for authentication methods
- ProviderCapabilities describing provider features and rate limits
- ConnectionCredentials unified credential container
- OAuthTokens for OAuth 2.0 tokens
- RemoteFile and RemoteFolder for normalized file metadata
- SyncDelta for incremental sync deltas
- CloudSourceProvider abstract interface with standardized methods

```mermaid
classDiagram
class ProviderCapabilities {
+provider_type
+display_name
+description
+icon
+supported_auth_types
+oauth_scopes
+supports_delta_sync
+supports_webhooks
+supports_file_streaming
+supports_folders
+supports_files
+supports_attachments
+rate_limit_requests_per_minute
+rate_limit_bytes_per_day
+documentation_url
+setup_instructions
}
class ConnectionCredentials {
+auth_type
+oauth_tokens
+api_key
+username
+password
+app_token
+certificate_path
+certificate_password
+server_url
+extra
}
class OAuthTokens {
+access_token
+refresh_token
+expires_at
+token_type
+scope
}
class RemoteFile {
+id
+name
+path
+mime_type
+size_bytes
+modified_at
+created_at
+checksum
+download_url
+parent_id
+web_view_url
+thumbnail_url
+version_id
+etag
+provider_metadata
}
class RemoteFolder {
+id
+name
+path
+parent_id
+children_count
+modified_at
+has_children
+is_root
+provider_metadata
}
class SyncDelta {
+added
+modified
+deleted
+next_delta_token
+has_more
+total_changes
}
ProviderCapabilities --> OAuthTokens
ConnectionCredentials --> OAuthTokens
CloudSourceProvider --> ProviderCapabilities
CloudSourceProvider --> RemoteFile
CloudSourceProvider --> RemoteFolder
CloudSourceProvider --> SyncDelta
```

**Diagram sources**
- [base.py](file://backend/providers/base.py#L18-L138)
- [base.py](file://backend/providers/base.py#L151-L177)
- [base.py](file://backend/providers/base.py#L141-L148)
- [base.py](file://backend/providers/base.py#L76-L120)
- [base.py](file://backend/providers/base.py#L123-L138)

**Section sources**
- [base.py](file://backend/providers/base.py#L18-L138)
- [base.py](file://backend/providers/base.py#L151-L177)
- [base.py](file://backend/providers/base.py#L205-L290)

### Google Drive Provider
Implements OAuth 2.0 authentication, file listing, streaming downloads, and delta sync via the Google Drive API. It exports Google Workspace documents to PDF/XLSX for indexing.

Key features:
- OAuth 2.0 with refresh token support
- Delta sync using Changes API
- Streaming downloads with Google Workspace export
- Storage quota and user info retrieval

```mermaid
sequenceDiagram
participant Client as "Client App"
participant GD as "GoogleDriveProvider"
participant API as "Google Drive API"
Client->>GD : authenticate(ConnectionCredentials)
GD->>API : GET /about (validate)
API-->>GD : 200 OK
GD-->>Client : True
Client->>GD : get_changes(delta_token)
GD->>API : GET /changes (paged)
API-->>GD : changes list
GD-->>Client : SyncDelta
Client->>GD : download_file(file_id)
GD->>API : GET /files/{id}/export or /files/{id}
API-->>GD : file content stream
GD-->>Client : AsyncIterator bytes
```

**Diagram sources**
- [google_drive.py](file://backend/providers/google_drive.py#L136-L176)
- [google_drive.py](file://backend/providers/google_drive.py#L516-L584)
- [google_drive.py](file://backend/providers/google_drive.py#L446-L475)

**Section sources**
- [google_drive.py](file://backend/providers/google_drive.py#L51-L91)
- [google_drive.py](file://backend/providers/google_drive.py#L136-L217)
- [google_drive.py](file://backend/providers/google_drive.py#L516-L584)

### Dropbox Provider
Implements OAuth 2.0 authentication, cursor-based delta sync, and streaming downloads via the Dropbox API v2.

Key features:
- OAuth 2.0 with refresh token support
- Cursor-based delta sync
- Streaming downloads with content endpoint
- Storage quota and user info retrieval

**Section sources**
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py#L38-L73)
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py#L127-L202)
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py#L478-L543)

### WebDAV/OwnCloud/Nextcloud Provider
Implements password and app token authentication, PROPFIND-based listing, and streaming downloads for WebDAV-compatible servers.

Key features:
- Password and app token authentication
- PROPFIND XML parsing for metadata
- Streaming downloads
- Quota retrieval via PROPFIND properties

**Section sources**
- [webdav.py](file://backend/providers/webdav.py#L61-L96)
- [webdav.py](file://backend/providers/webdav.py#L260-L307)
- [webdav.py](file://backend/providers/webdav.py#L459-L473)

### Airbyte Integration Pattern
Enterprise providers (Confluence, Jira, Gmail, Outlook, IMAP) inherit from AirbyteProvider, which manages Airbyte sources, destinations, and sync jobs. The base class supports profile-aware configuration and collection prefixing.

```mermaid
classDiagram
class AirbyteProvider {
<<abstract>>
+airbyte_url
+mongodb_uri
+mongodb_database
+collection_prefix
+client
+source_definition_id
+source_display_name
+build_source_config(credentials)
+get_default_streams()
+transform_record(stream_name, record)
+setup_airbyte_resources(source_name, streams)
+get_or_create_resources(source_id, destination_id, connection_id)
+authenticate(credentials)
+validate_credentials()
+refresh_credentials()
+trigger_sync()
+wait_for_sync(job_id, poll_interval, timeout)
+sync_and_wait()
+list_root_folders()
+list_folder_contents(folder_id, include_files, include_folders)
+get_file_metadata(file_id)
+download_file(file_id)
+list_all_files(folder_id, recursive, file_types)
+delete_resources()
+close()
}
class ConfluenceProvider
class JiraProvider
class GmailProvider
class OutlookProvider
class ImapProvider
AirbyteProvider <|-- ConfluenceProvider
AirbyteProvider <|-- JiraProvider
AirbyteProvider <|-- GmailProvider
AirbyteProvider <|-- OutlookProvider
AirbyteProvider <|-- ImapProvider
```

**Diagram sources**
- [base.py](file://backend/providers/airbyte/base.py#L52-L566)
- [confluence.py](file://backend/providers/airbyte/confluence.py#L27-L75)
- [jira.py](file://backend/providers/airbyte/jira.py#L27-L76)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L27-L73)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L27-L74)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L27-L91)

**Section sources**
- [base.py](file://backend/providers/airbyte/base.py#L66-L141)
- [base.py](file://backend/providers/airbyte/base.py#L200-L374)
- [base.py](file://backend/providers/airbyte/base.py#L418-L453)

### Confluence Provider
- Supports OAuth 2.0 and API key authentication
- Streams: pages, blog_posts, spaces, page_attachments
- Transforms records to RemoteFile with HTML content
- Delta sync via Airbyte job completion

**Section sources**
- [confluence.py](file://backend/providers/airbyte/confluence.py#L27-L75)
- [confluence.py](file://backend/providers/airbyte/confluence.py#L85-L124)
- [confluence.py](file://backend/providers/airbyte/confluence.py#L135-L264)
- [confluence.py](file://backend/providers/airbyte/confluence.py#L310-L336)

### Jira Provider
- Supports OAuth 2.0 and API key authentication
- Streams: issues, projects, issue_comments, issue_fields, users, sprints
- Transforms records to Markdown content with custom fields
- Delta sync via Airbyte job completion

**Section sources**
- [jira.py](file://backend/providers/airbyte/jira.py#L27-L76)
- [jira.py](file://backend/providers/airbyte/jira.py#L86-L133)
- [jira.py](file://backend/providers/airbyte/jira.py#L146-L311)
- [jira.py](file://backend/providers/airbyte/jira.py#L350-L369)

### Gmail Provider
- OAuth 2.0 authentication via Google OAuth
- Streams: messages, threads, labels
- Transforms messages/threads to RemoteFile with extracted body content
- Delta sync via Airbyte job completion

**Section sources**
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L27-L73)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L83-L110)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L120-L198)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L347-L366)

### Outlook Provider
- OAuth 2.0 authentication via Microsoft OAuth
- Streams: messages, mail_folders
- Transforms messages to RemoteFile with body content
- Delta sync via Airbyte job completion

**Section sources**
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L27-L74)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L84-L116)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L125-L223)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L282-L301)

### IMAP Provider
- Password authentication for generic IMAP servers
- Streams: messages, folders
- Non-destructive sync by default (read-only, peek mode, preserve flags)
- Delta sync via UIDVALIDITY

**Section sources**
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L27-L91)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L101-L212)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L221-L305)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L374-L393)

### Provider Discovery Router
Lists available providers and their capabilities, including supported auth types, delta/webhook support, documentation links, and setup instructions.

**Section sources**
- [providers.py](file://backend/routers/cloud_sources/providers.py#L24-L147)
- [providers.py](file://backend/routers/cloud_sources/providers.py#L150-L178)

### OAuth Router
Manages OAuth 2.0 flows for supported providers:
- Initiates authorization with state generation
- Handles callbacks and token exchange
- Stores encrypted-like credentials and user info
- Provides manual token refresh and revocation placeholders

```mermaid
flowchart TD
Start([Initiate OAuth]) --> GenState["Generate state token<br/>Store state mapping"]
GenState --> BuildAuth["Build authorization URL<br/>Add scopes and provider params"]
BuildAuth --> Redirect["Redirect user to provider consent"]
Redirect --> Callback["OAuth callback with code + state"]
Callback --> ValidateState["Validate state and expiry"]
ValidateState --> Exchange["Exchange code for tokens"]
Exchange --> UserInfo["Fetch user info"]
UserInfo --> Save["Insert connection document"]
Save --> Success(["Redirect success"])
```

**Diagram sources**
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L155-L218)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L221-L319)

**Section sources**
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L128-L146)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L155-L218)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L221-L319)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L400-L468)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L500-L536)

## Dependency Analysis
Provider relationships and dependencies:

```mermaid
graph TB
REG[registry.py] --> GD[google_drive.py]
REG --> DB[dropbox_provider.py]
REG --> WD[webdav.py]
REG --> CF[airbyte/confluence.py]
REG --> JR[airbyte/jira.py]
REG --> GG[airbyte/email_gmail.py]
REG --> GO[airbyte/email_outlook.py]
REG --> IM[airbyte/email_imap.py]
GD --> BASE[base.py]
DB --> BASE
WD --> BASE
CF --> AB[airbyte/base.py]
JR --> AB
GG --> AB
GO --> AB
IM --> AB
PR[cloud_sources/providers.py] --> REG
OA[cloud_sources/oauth.py] --> REG
```

**Diagram sources**
- [registry.py](file://backend/providers/registry.py#L79-L142)
- [google_drive.py](file://backend/providers/google_drive.py#L30-L31)
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py#L28-L29)
- [webdav.py](file://backend/providers/webdav.py#L30-L31)
- [confluence.py](file://backend/providers/airbyte/confluence.py#L21-L22)
- [jira.py](file://backend/providers/airbyte/jira.py#L21-L22)
- [email_gmail.py](file://backend/providers/airbyte/email_gmail.py#L21-L22)
- [email_outlook.py](file://backend/providers/airbyte/email_outlook.py#L21-L22)
- [email_imap.py](file://backend/providers/airbyte/email_imap.py#L21-L22)
- [base.py](file://backend/providers/base.py#L11-L16)
- [base.py](file://backend/providers/airbyte/base.py#L28-L39)
- [providers.py](file://backend/routers/cloud_sources/providers.py#L11-L17)
- [oauth.py](file://backend/routers/cloud_sources/oauth.py#L17-L29)

**Section sources**
- [registry.py](file://backend/providers/registry.py#L79-L142)
- [base.py](file://backend/providers/base.py#L11-L16)
- [base.py](file://backend/providers/airbyte/base.py#L28-L39)

## Performance Considerations
- Rate limits: Providers declare per-minute and daily limits in ProviderCapabilities. Respect these limits to avoid throttling.
- Streaming downloads: Use download_file streaming to avoid loading entire files into memory.
- Delta sync: Prefer providers supporting delta sync (Google Drive, Dropbox, Airbyte-backed) to minimize API calls.
- Pagination: Use cursor-based pagination (Dropbox) or pageToken-based pagination (Google Drive) to handle large datasets efficiently.
- Concurrency: AirbyteProvider uses async operations; ensure proper resource cleanup and connection pooling.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Authentication failures: Verify OAuth client credentials and scopes. Check token expiration and refresh tokens.
- Rate limit errors: Implement backoff and retry logic. Monitor ProviderCapabilities.rate_limit_*.
- Provider not available: Confirm provider registration and environment variables for OAuth client IDs/secrets.
- Airbyte connectivity: Ensure Airbyte is running and reachable. Check workspace/destination IDs and MongoDB connectivity.
- WebDAV errors: Validate server URL, credentials, and WebDAV path encoding.

**Section sources**
- [base.py](file://backend/providers/base.py#L505-L525)
- [google_drive.py](file://backend/providers/google_drive.py#L117-L128)
- [dropbox_provider.py](file://backend/providers/dropbox_provider.py#L103-L119)
- [webdav.py](file://backend/providers/webdav.py#L152-L159)
- [base.py](file://backend/providers/airbyte/base.py#L224-L230)

## Conclusion
The cloud source integration leverages a robust provider registry and unified base interface to support diverse cloud providers. OAuth flows are centralized in router endpoints, while enterprise integrations use Airbyte for reliable, scalable sync. The design enables extensibility through the CloudSourceProvider interface and AirbyteProvider base class, allowing new providers to be added with minimal boilerplate.