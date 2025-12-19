"""Direct Email Sync Providers

This module provides direct email synchronization without Airbyte dependency.
Supports:
- IMAP (any email provider)
- Gmail (via Gmail API)
- Outlook (via Microsoft Graph API)

Features:
- Deep folder enumeration (10+ levels)
- Large-scale sync (1M+ emails with batching)
- Resumable checkpoint system
- Memory-efficient streaming
- Non-destructive read-only sync
"""

from backend.providers.email.base import (
    EmailSyncConfig,
    EmailSyncState,
    EmailFolder,
    EmailMessage,
    EmailSyncProgress,
)

# Optional imports - these may not be available if dependencies aren't installed
try:
    from backend.providers.email.imap_sync import DirectImapSync
except ImportError:
    DirectImapSync = None

try:
    from backend.providers.email.gmail_sync import DirectGmailSync
except ImportError:
    DirectGmailSync = None

try:
    from backend.providers.email.outlook_sync import DirectOutlookSync
except ImportError:
    DirectOutlookSync = None

__all__ = [
    # Always available
    "EmailSyncConfig",
    "EmailSyncState",
    "EmailFolder",
    "EmailMessage",
    "EmailSyncProgress",
    # Optional (may be None if deps missing)
    "DirectImapSync",
    "DirectGmailSync",
    "DirectOutlookSync",
]
