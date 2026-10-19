"""Profile management for multi-project support.

Each profile defines a separate project with its own:
- Document folder(s)
- MongoDB database
- Collection names
- Search index names
- Airbyte integration settings
- Cloud source connections

Both tenant backends (RecallHub, Quellex) share one ``profiles.yaml`` on a host
that runs both. A profile records its owning tenant (``ProfileConfig.tenant``),
a :class:`ProfileManager` bound to a tenant only sees its own and unassigned
profiles, and every write locks the file, re-reads it and applies one change, so
the backends no longer undo each other's edits. See
``docs/27-TENANT_ISOLATION.md``.
"""

import os
import threading
import yaml
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Dict, Any, List, TextIO, TypeVar
from pydantic import BaseModel, Field, field_validator
from enum import Enum

try:  # POSIX: the backends and the ingestion worker run in Linux containers
    import fcntl
except ImportError:  # pragma: no cover - Windows dev runs fall back to an in-process lock
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Default profiles configuration file path
DEFAULT_PROFILES_PATH = "profiles.yaml"

#: Owning tenant of profiles written before ``ProfileConfig.tenant`` existed, by key.
#: Profiles neither tagged nor listed here are unassigned (visible to every tenant).
#: Mirrored by ``Get-ProfileTenants`` in scripts/full-backup.ps1 and scripts/full-restore.ps1.
LEGACY_PROFILE_TENANTS: Dict[str, str] = {
    "diejuristin": "quellex",
    "parhelion": "recallhub",
    "parhelion-full": "recallhub",
}

_T = TypeVar("_T")


def normalize_tenant(value: Optional[str]) -> Optional[str]:
    """Normalise a tenant id: stripped and lower-cased, blank becomes ``None``."""
    if value is None:
        return None
    tenant = str(value).strip().lower()
    return tenant or None


class CloudSourceType(str, Enum):
    """Supported cloud source types."""
    GOOGLE_DRIVE = "google_drive"
    DROPBOX = "dropbox"
    ONEDRIVE = "onedrive"
    WEBDAV = "webdav"
    CONFLUENCE = "confluence"
    JIRA = "jira"
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    IMAP = "imap"


class CloudSourceAssociation(BaseModel):
    """Association between a profile and a cloud source connection.
    
    Each profile can have multiple cloud source connections,
    and each connection syncs data to the profile's database.
    """
    
    # Connection identification
    connection_id: str = Field(..., description="Unique ID of the cloud source connection")
    provider_type: CloudSourceType = Field(..., description="Type of cloud provider")
    display_name: str = Field(default="", description="User-friendly name for this connection")
    
    # Airbyte-specific IDs (for Airbyte-backed providers)
    airbyte_source_id: Optional[str] = Field(default=None, description="Airbyte source ID")
    airbyte_connection_id: Optional[str] = Field(default=None, description="Airbyte sync connection ID")
    
    # Sync configuration
    enabled: bool = Field(default=True, description="Whether sync is enabled")
    sync_schedule: Optional[str] = Field(default=None, description="Cron schedule for sync (e.g., '0 */6 * * *')")
    last_sync_at: Optional[str] = Field(default=None, description="ISO timestamp of last sync")
    last_sync_status: Optional[str] = Field(default=None, description="Status of last sync (success/error)")
    
    # Folder/path filters
    include_paths: List[str] = Field(default_factory=list, description="Paths/folders to include")
    exclude_paths: List[str] = Field(default_factory=list, description="Paths/folders to exclude")
    
    # Collection prefix for synced data
    collection_prefix: str = Field(default="", description="Prefix for MongoDB collections from this source")


class AirbyteConfig(BaseModel):
    """Airbyte-specific configuration for a profile.
    
    Each profile can have its own Airbyte workspace and destination,
    allowing complete isolation of synced data.
    """
    
    # Airbyte workspace (one per profile for isolation)
    workspace_id: Optional[str] = Field(default=None, description="Airbyte workspace ID for this profile")
    workspace_name: Optional[str] = Field(default=None, description="Airbyte workspace name")
    
    # Airbyte destination (points to profile's MongoDB database)
    destination_id: Optional[str] = Field(default=None, description="Airbyte MongoDB destination ID")
    
    # Default sync settings
    default_sync_mode: str = Field(default="incremental", description="Default sync mode (full_refresh/incremental)")
    default_schedule_type: str = Field(default="manual", description="Default schedule type (manual/scheduled)")
    default_schedule_cron: Optional[str] = Field(default=None, description="Default cron expression for scheduled syncs")


class ProfileConfig(BaseModel):
    """Configuration for a single profile.
    
    A profile represents a complete project workspace with:
    - Document storage (local folders)
    - MongoDB database for RAG data
    - Cloud source integrations (via Airbyte)
    - Search indexes
    """
    
    name: str = Field(..., description="Display name for the profile")
    description: str = Field(default="", description="Profile description")

    # Owning tenant (TENANT_ID of the backend that may use it); None = unassigned/shared
    tenant: Optional[str] = Field(
        default=None,
        description="Tenant owning this profile (recallhub, quellex); unset = visible to every tenant",
    )

    # Owner/access control (for multi-user scenarios)
    owner_user_id: Optional[str] = Field(default=None, description="User ID of the profile owner")
    
    # Document sources
    documents_folders: List[str] = Field(
        default_factory=lambda: ["documents"],
        description="List of document folders for this profile"
    )
    
    # MongoDB configuration
    database: str = Field(default="rag_db", description="MongoDB database name")
    collection_documents: str = Field(default="documents", description="Documents collection")
    collection_chunks: str = Field(default="chunks", description="Chunks collection")
    
    # Search index names
    vector_index: str = Field(default="vector_index", description="Vector search index name")
    text_index: str = Field(default="text_index", description="Text search index name")
    
    # Optional overrides
    embedding_model: Optional[str] = Field(default=None, description="Override embedding model")
    llm_model: Optional[str] = Field(default=None, description="Override LLM model")
    
    # Airbyte integration
    airbyte: Optional[AirbyteConfig] = Field(
        default=None, 
        description="Airbyte configuration for this profile"
    )
    
    # Cloud source connections
    cloud_sources: List[CloudSourceAssociation] = Field(
        default_factory=list,
        description="Cloud source connections associated with this profile"
    )

    @field_validator("tenant", mode="before")
    @classmethod
    def _normalize_tenant(cls, value: Optional[str]) -> Optional[str]:
        return normalize_tenant(value)

    def get_cloud_source(self, connection_id: str) -> Optional[CloudSourceAssociation]:
        """Get a cloud source by connection ID."""
        for source in self.cloud_sources:
            if source.connection_id == connection_id:
                return source
        return None
    
    def get_cloud_sources_by_type(self, provider_type: CloudSourceType) -> List[CloudSourceAssociation]:
        """Get all cloud sources of a specific type."""
        return [s for s in self.cloud_sources if s.provider_type == provider_type]
    
    def add_cloud_source(self, source: CloudSourceAssociation) -> bool:
        """Add a cloud source association."""
        if self.get_cloud_source(source.connection_id):
            return False  # Already exists
        self.cloud_sources.append(source)
        return True
    
    def remove_cloud_source(self, connection_id: str) -> bool:
        """Remove a cloud source association."""
        for i, source in enumerate(self.cloud_sources):
            if source.connection_id == connection_id:
                self.cloud_sources.pop(i)
                return True
        return False


class ProfilesConfig(BaseModel):
    """Root configuration containing all profiles."""

    active_profile: str = Field(
        default="default",
        description="Active profile of processes without a tenant (worker, CLI, local runs)",
    )
    active_profiles: Dict[str, str] = Field(
        default_factory=dict,
        description="Persisted active profile per tenant (written by tenant-bound backends)",
    )
    profiles: Dict[str, ProfileConfig] = Field(
        default_factory=dict,
        description="Dictionary of profile configurations"
    )


def profile_tenant(key: str, profile: ProfileConfig) -> Optional[str]:
    """Effective owning tenant of a profile.

    Args:
        key: Profile key.
        profile: The profile's configuration.

    Returns:
        The ``tenant`` field, else the legacy owner of a known key
        (:data:`LEGACY_PROFILE_TENANTS`), else ``None`` (unassigned).
    """
    return normalize_tenant(profile.tenant) or LEGACY_PROFILE_TENANTS.get(key)


def profile_visible_to(key: str, profile: ProfileConfig, tenant: Optional[str]) -> bool:
    """Whether a backend of ``tenant`` may see a profile.

    Args:
        key: Profile key.
        profile: The profile's configuration.
        tenant: The backend's tenant; ``None`` (worker, CLI) sees everything.

    Returns:
        True for the tenant's own and for unassigned profiles.
    """
    tenant = normalize_tenant(tenant)
    if tenant is None:
        return True
    owner = profile_tenant(key, profile)
    return owner is None or owner == tenant


_lock_fallback_logged = False


def _lock_file(handle: TextIO, exclusive: bool) -> bool:
    """Take a blocking ``flock`` on an open file.

    The lock is taken on ``profiles.yaml`` itself because only that file is
    bind-mounted into the containers; a sidecar lock file would not be shared.

    Returns:
        True when locked, False when locking is unavailable (Windows, or a
        filesystem that refuses ``flock``); the caller then relies on the
        in-process lock and on re-reading before every write.
    """
    global _lock_fallback_logged
    if fcntl is None:
        reason = "fcntl is not available on this platform"
    else:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            return True
        except OSError as exc:
            reason = f"flock failed: {exc}"
    if not _lock_fallback_logged:
        logger.warning(f"profiles.yaml is not file-locked ({reason}); using an in-process lock only")
        _lock_fallback_logged = True
    return False


def _unlock_file(handle: TextIO) -> None:
    """Release a lock taken by :func:`_lock_file`."""
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:  # closing the file releases it anyway
            pass


class ProfileManager:
    """Manages profile loading, switching, and persistence.

    The active profile is per process: it starts from ``ACTIVE_PROFILE`` (see
    ``src/settings.py``) or the file, survives :meth:`reload`, and a switch is
    written back only when asked to (and then into the tenant's own slot).

    A manager bound to a tenant (``tenant_id``) only lists, returns, switches to
    and edits the tenant's own and unassigned profiles. Unbound managers (the
    shared ingestion worker, the CLI) see every profile.
    """

    def __init__(self, profiles_path: Optional[str] = None, tenant_id: Optional[str] = None):
        """
        Initialize profile manager.

        Args:
            profiles_path: Path to profiles.yaml file. Uses default if not specified.
            tenant_id: Tenant this manager serves; ``None`` sees every profile.
        """
        self.profiles_path = Path(profiles_path or DEFAULT_PROFILES_PATH)
        self.tenant_id: Optional[str] = normalize_tenant(tenant_id)
        self._config: Optional[ProfilesConfig] = None
        #: Key of this process's active profile; see _settle_active().
        self._active_key: Optional[str] = None
        #: Modification time of the file as last read or written; see reload().
        self._loaded_mtime_ns: Optional[int] = None
        self._lock = threading.RLock()
        self._load_profiles()
        self._remember_mtime()

    def _remember_mtime(self) -> None:
        """Record the on-disk modification time of ``profiles.yaml``."""
        try:
            self._loaded_mtime_ns = self.profiles_path.stat().st_mtime_ns
        except OSError:
            self._loaded_mtime_ns = None

    # ==================== Loading and persistence ====================

    @contextmanager
    def _open_locked(self, exclusive: bool) -> Iterator[TextIO]:
        """Open ``profiles.yaml`` under a shared (read) or exclusive (write) file lock."""
        if exclusive and not self.profiles_path.exists():
            self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
            self.profiles_path.touch()
        with open(self.profiles_path, "r+" if exclusive else "r", encoding="utf-8") as handle:
            locked = _lock_file(handle, exclusive)
            try:
                yield handle
            finally:
                if locked:
                    _unlock_file(handle)

    @staticmethod
    def _parse(data: Dict[str, Any]) -> ProfilesConfig:
        """Build a :class:`ProfilesConfig` from the parsed YAML mapping."""
        profiles = {
            str(key): ProfileConfig(**(value or {}))
            for key, value in (data.get('profiles') or {}).items()
        }
        active_profiles: Dict[str, str] = {}
        for tenant, key in (data.get('active_profiles') or {}).items():
            tenant_id = normalize_tenant(tenant)
            if tenant_id and key:
                active_profiles[tenant_id] = str(key)
        return ProfilesConfig(
            active_profile=data.get('active_profile') or 'default',
            active_profiles=active_profiles,
            profiles=profiles,
        )

    @staticmethod
    def _dump(config: ProfilesConfig) -> str:
        """Serialise a configuration in the layout of the hand-written file."""
        data: Dict[str, Any] = {'active_profile': config.active_profile}
        if config.active_profiles:
            data['active_profiles'] = dict(sorted(config.active_profiles.items()))
        data['profiles'] = {
            key: profile.model_dump(mode="json", exclude_none=True)
            for key, profile in config.profiles.items()
        }
        return yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _load_profiles(self) -> None:
        """Load profiles from YAML configuration file."""
        with self._lock:
            if not self.profiles_path.exists():
                logger.info(f"No profiles file found at {self.profiles_path}, creating default")
                self._config = self._create_default_config()
                self._settle_active()
                self._save_profiles()
                return

            try:
                with self._open_locked(exclusive=False) as handle:
                    text = handle.read()
                if not text.strip() and self._config is not None:
                    logger.warning(f"{self.profiles_path} is empty; keeping the profiles loaded before")
                    return
                self._config = self._parse(yaml.safe_load(text) or {})
                logger.info(
                    f"Loaded {len(self._config.profiles)} profiles, active: {self._config.active_profile}"
                )
            except Exception as e:
                logger.error(f"Failed to load profiles: {e}")
                if self._config is None:
                    self._config = self._create_default_config()
            self._settle_active()

    def _settle_active(self) -> None:
        """Pick this process's active profile after a load.

        A profile already chosen in this process is kept as long as it still
        exists, so a reload never moves a running backend to another database.
        Otherwise the tenant's persisted slot, then the file-wide
        ``active_profile``, then the first visible profile is used; profiles
        of another tenant are skipped.
        """
        if not self._config:
            return
        profiles = self._config.profiles
        if self._active_key in profiles:
            return

        candidates: List[Optional[str]] = []
        if self.tenant_id:
            candidates.append(self._config.active_profiles.get(self.tenant_id))
        candidates.append(self._config.active_profile)
        for key in candidates:
            if key and key in profiles:
                if self._visible(key):
                    self._active_key = key
                    return
                logger.warning(
                    f"Active profile '{key}' in {self.profiles_path} belongs to tenant "
                    f"'{profile_tenant(key, profiles[key])}', not '{self.tenant_id}'; skipping it"
                )

        visible = [key for key in profiles if self._visible(key)]
        if "default" in visible:
            self._active_key = "default"
        elif visible:
            self._active_key = visible[0]
        else:
            # Nothing usable: keep the file's key so active_profile falls back to the built-in default
            self._active_key = self._config.active_profile

    def _mutate(self, apply: Callable[[ProfilesConfig], _T]) -> _T:
        """Apply one change to the current on-disk configuration and write it back.

        Takes an exclusive lock on ``profiles.yaml``, re-reads it (so changes
        written by another backend are kept), calls ``apply`` on that fresh
        copy and rewrites the file in place when ``apply`` returns a truthy
        value. In-place because a bind-mounted file cannot be replaced by a
        rename.

        Args:
            apply: Mutates the configuration it is given; returns a falsy value
                to skip the write.

        Returns:
            What ``apply`` returned; ``None`` when the file could not be read
            or written (logged).
        """
        with self._lock:
            try:
                with self._open_locked(exclusive=True) as handle:
                    text = handle.read()
                    if text.strip():
                        config = self._parse(yaml.safe_load(text) or {})
                    else:
                        base = self._config or self._create_default_config()
                        config = base.model_copy(deep=True)
                    result = apply(config)
                    if result:
                        handle.seek(0)
                        handle.truncate()
                        handle.write(self._dump(config))
                        handle.flush()
                        os.fsync(handle.fileno())
            except Exception as e:
                logger.error(f"Failed to update {self.profiles_path}: {e}")
                return None  # type: ignore[return-value]

            self._config = config
            self._remember_mtime()
            self._settle_active()
            if result:
                logger.info(f"Saved profiles to {self.profiles_path}")
            return result

    def _create_default_config(self) -> ProfilesConfig:
        """Create default configuration with a single 'default' profile."""
        default_profile = ProfileConfig(
            name="Default",
            description="Default project profile",
            documents_folders=["documents"],
            database="rag_db",
            collection_documents="documents",
            collection_chunks="chunks",
            vector_index="vector_index",
            text_index="text_index"
        )

        return ProfilesConfig(
            active_profile="default",
            profiles={"default": default_profile}
        )

    def _save_profiles(self) -> None:
        """Write the in-memory configuration when the file is missing or empty.

        Existing content is never overwritten from memory; use :meth:`_mutate`.
        """
        self._mutate(lambda config: True)

    # ==================== Tenant scoping ====================

    def bind_tenant(self, tenant_id: Optional[str]) -> None:
        """Scope this manager to a tenant (the API lifespan binds ``settings.tenant_id``).

        The active profile is not changed: the process already connected to its
        database. An active profile owned by another tenant is only reported.
        """
        tenant = normalize_tenant(tenant_id)
        with self._lock:
            if tenant == self.tenant_id:
                return
            if self.tenant_id is not None:
                logger.warning(f"Profile manager re-bound from tenant '{self.tenant_id}' to '{tenant}'")
            self.tenant_id = tenant
            if self._config and self._active_key in self._config.profiles and not self._visible(self._active_key):
                logger.error(
                    f"Active profile '{self._active_key}' belongs to tenant "
                    f"'{self.profile_tenant(self._active_key)}', not '{tenant}'"
                )

    def _visible(self, key: str) -> bool:
        """Whether profile ``key`` is visible to this manager's tenant."""
        if not self._config or key not in self._config.profiles:
            return False
        return profile_visible_to(key, self._config.profiles[key], self.tenant_id)

    def profile_tenant(self, key: str) -> Optional[str]:
        """Effective owning tenant of profile ``key`` (None when unassigned or unknown)."""
        if not self._config or key not in self._config.profiles:
            return None
        return profile_tenant(key, self._config.profiles[key])

    def profile_exists(self, key: str) -> bool:
        """Whether a profile key is taken, by any tenant (the key namespace is shared)."""
        return bool(self._config and key in self._config.profiles)

    def list_all_profiles(self) -> Dict[str, ProfileConfig]:
        """Every profile in the file regardless of tenant (worker, CLI, conflict checks)."""
        if not self._config:
            return {}
        return self._config.profiles

    # ==================== Active profile ====================

    @property
    def active_profile_name(self) -> str:
        """Get the name of the currently active profile."""
        return self._active_key or "default"

    @property
    def active_profile_key(self) -> str:
        """Get the key of the currently active profile (alias for active_profile_name)."""
        return self.active_profile_name

    @property
    def active_profile(self) -> ProfileConfig:
        """Get the currently active profile configuration."""
        if not self._config:
            return self._create_default_config().profiles["default"]

        profile = self._config.profiles.get(self.active_profile_name)
        if not profile:
            logger.warning(f"Active profile '{self.active_profile_name}' not found, using default")
            if "default" in self._config.profiles:
                return self._config.profiles["default"]
            return self._create_default_config().profiles["default"]

        return profile

    def list_profiles(self) -> Dict[str, ProfileConfig]:
        """Get the profiles visible to this manager's tenant (all when unbound)."""
        if not self._config:
            return {}
        if self.tenant_id is None:
            return self._config.profiles
        return {
            key: profile for key, profile in self._config.profiles.items()
            if profile_visible_to(key, profile, self.tenant_id)
        }

    def get_profile(self, name: str) -> Optional[ProfileConfig]:
        """Get a specific profile by name (None when unknown or another tenant's)."""
        if not self._config or not self._visible(name):
            return None
        return self._config.profiles.get(name)

    def reload(self, force: bool = False) -> bool:
        """Re-read ``profiles.yaml`` when it changed on disk.

        The API process edits profiles through this manager, but the ingestion
        worker runs in its own process with a read-only copy of the file, the
        other tenant's backend writes the same file, and administrators may
        edit it by hand. Callers that are about to scan folders (ingestion
        jobs) reload first so new folders are seen without a restart. The
        process's active profile is kept.

        Args:
            force: Re-read even when the modification time is unchanged.

        Returns:
            True when the file was re-read, False when it was unchanged or
            missing (the in-memory configuration is kept in both cases).
        """
        try:
            mtime = self.profiles_path.stat().st_mtime_ns
        except OSError:
            return False
        if not force and mtime == self._loaded_mtime_ns:
            return False
        self._load_profiles()
        self._loaded_mtime_ns = mtime
        return True

    def switch_profile(self, name: str, persist: bool = True, force: bool = False) -> bool:
        """
        Switch to a different profile.

        Args:
            name: Profile name to switch to
            persist: Write the new active profile to ``profiles.yaml``: into
                ``active_profiles.<tenant>`` for a tenant-bound manager, into
                the file-wide ``active_profile`` otherwise. Pass False for
                in-memory switches (``ACTIVE_PROFILE``, the ingestion worker
                with its read-only copy of the file).
            force: Switch even to another tenant's profile (an explicit
                ``ACTIVE_PROFILE`` pin); logged as an error.

        Returns:
            True if switch was successful, False if the profile is unknown or
            belongs to another tenant
        """
        with self._lock:
            if not self._config:
                return False

            if name not in self._config.profiles:
                logger.error(f"Profile '{name}' not found")
                return False

            if not self._visible(name):
                owner = self.profile_tenant(name)
                if not force:
                    logger.error(f"Profile '{name}' belongs to tenant '{owner}', not '{self.tenant_id}'")
                    return False
                logger.error(
                    f"Using profile '{name}' of tenant '{owner}' in tenant '{self.tenant_id}' "
                    "because ACTIVE_PROFILE pins it"
                )

            self._active_key = name
            if persist:
                tenant = self.tenant_id

                def apply(config: ProfilesConfig) -> bool:
                    if name not in config.profiles:
                        return False
                    if tenant:
                        config.active_profiles[tenant] = name
                    else:
                        config.active_profile = name
                    return True

                self._mutate(apply)
            logger.info(f"Switched to profile: {name}")
            return True

    # ==================== Profile CRUD ====================

    def create_profile(
        self,
        key: str,
        name: str,
        documents_folders: List[str],
        database: Optional[str] = None,
        description: str = "",
        tenant: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Create a new profile.

        Args:
            key: Profile key/identifier (used in CLI)
            name: Display name
            documents_folders: List of document folder paths
            database: MongoDB database name (defaults to rag_{key})
            description: Profile description
            tenant: Owning tenant; defaults to the manager's tenant (unassigned when unbound)
            **kwargs: Additional ProfileConfig fields

        Returns:
            True if creation was successful
        """
        if not self._config:
            return False

        # Create profile with sensible defaults based on key
        profile = ProfileConfig(
            name=name,
            description=description or "",  # the API sends null when left empty
            tenant=tenant if tenant is not None else self.tenant_id,
            documents_folders=documents_folders,
            database=database or f"rag_{key}",
            collection_documents=kwargs.get('collection_documents', 'documents'),
            collection_chunks=kwargs.get('collection_chunks', 'chunks'),
            vector_index=kwargs.get('vector_index', 'vector_index'),
            text_index=kwargs.get('text_index', 'text_index'),
            embedding_model=kwargs.get('embedding_model'),
            llm_model=kwargs.get('llm_model')
        )

        def apply(config: ProfilesConfig) -> bool:
            if key in config.profiles:
                logger.error(f"Profile '{key}' already exists")
                return False
            config.profiles[key] = profile
            return True

        if not self._mutate(apply):
            return False
        logger.info(f"Created profile: {key} (tenant: {profile.tenant or 'unassigned'})")
        return True

    def _edit_profile(self, key: str, edit: Callable[[ProfileConfig], _T]) -> Optional[_T]:
        """Apply ``edit`` to profile ``key`` in a fresh copy of the file and save it.

        Returns:
            What ``edit`` returned, or None when the profile is unknown or
            belongs to another tenant (nothing is written then).
        """
        tenant = self.tenant_id

        def apply(config: ProfilesConfig) -> Optional[_T]:
            profile = config.profiles.get(key)
            if profile is None:
                logger.error(f"Profile '{key}' not found")
                return None
            if not profile_visible_to(key, profile, tenant):
                logger.error(f"Profile '{key}' belongs to tenant '{profile_tenant(key, profile)}', not '{tenant}'")
                return None
            return edit(profile)

        return self._mutate(apply)

    def update_profile(
        self,
        key: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        documents_folders: Optional[List[str]] = None,
        database: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Update an existing profile.

        Args:
            key: Profile key to update
            name: New display name (optional)
            description: New description (optional)
            documents_folders: New list of document folders (optional)
            database: New database name (optional)
            **kwargs: Additional ProfileConfig fields; ``tenant`` sets the owner
                (``""`` releases the profile to unassigned)

        Returns:
            True if update was successful
        """
        if not self._config:
            return False

        def edit(profile: ProfileConfig) -> bool:
            # Update only provided fields
            if name is not None:
                profile.name = name
            if description is not None:
                profile.description = description
            if documents_folders is not None:
                profile.documents_folders = documents_folders
            if database is not None:
                profile.database = database
            if 'tenant' in kwargs and kwargs['tenant'] is not None:
                profile.tenant = normalize_tenant(kwargs['tenant'])

            # Handle additional kwargs
            for field in ['collection_documents', 'collection_chunks', 'vector_index', 'text_index', 'embedding_model', 'llm_model']:
                if field in kwargs and kwargs[field] is not None:
                    setattr(profile, field, kwargs[field])
            return True

        if not self._edit_profile(key, edit):
            return False
        logger.info(f"Updated profile: {key}")
        return True

    def save_profile(self, key: str, profile: ProfileConfig) -> bool:
        """Replace the stored configuration of an existing profile.

        Args:
            key: Profile key.
            profile: The new configuration (its ``tenant`` must keep the
                profile visible to this manager).

        Returns:
            True when saved.
        """
        replacement = profile.model_copy(deep=True)
        if not profile_visible_to(key, replacement, self.tenant_id):
            logger.error(f"Refusing to give profile '{key}' to tenant '{profile_tenant(key, replacement)}'")
            return False
        tenant = self.tenant_id

        def apply(config: ProfilesConfig) -> bool:
            current = config.profiles.get(key)
            if current is None or not profile_visible_to(key, current, tenant):
                logger.error(f"Profile '{key}' not found")
                return False
            config.profiles[key] = replacement
            return True

        return bool(self._mutate(apply))

    def delete_profile(self, name: str) -> bool:
        """
        Delete a profile.

        Args:
            name: Profile name to delete

        Returns:
            True if deletion was successful
        """
        if not self._config:
            return False

        if name == "default":
            logger.error("Cannot delete the default profile")
            return False

        if name == self.active_profile_name:
            logger.error("Cannot delete the active profile. Switch to another profile first.")
            return False

        tenant = self.tenant_id

        def apply(config: ProfilesConfig) -> bool:
            profile = config.profiles.get(name)
            if profile is None or not profile_visible_to(name, profile, tenant):
                logger.error(f"Profile '{name}' not found")
                return False
            del config.profiles[name]
            return True

        if not self._mutate(apply):
            return False
        logger.info(f"Deleted profile: {name}")
        return True

    def get_all_document_folders(self) -> List[str]:
        """Get all document folders for the active profile."""
        profile = self.active_profile
        return profile.documents_folders

    def get_primary_document_folder(self) -> str:
        """Get the primary (first) document folder for the active profile."""
        folders = self.get_all_document_folders()
        return folders[0] if folders else "documents"

    # ==================== Cloud Source Management ====================

    def add_cloud_source(
        self,
        profile_key: str,
        connection_id: str,
        provider_type: CloudSourceType,
        display_name: str = "",
        airbyte_source_id: Optional[str] = None,
        airbyte_connection_id: Optional[str] = None,
        collection_prefix: Optional[str] = None,
        **kwargs
    ) -> Optional[CloudSourceAssociation]:
        """
        Add a cloud source connection to a profile.

        Args:
            profile_key: Profile to add the cloud source to
            connection_id: Unique connection identifier
            provider_type: Type of cloud provider
            display_name: User-friendly name
            airbyte_source_id: Airbyte source ID (for Airbyte-backed providers)
            airbyte_connection_id: Airbyte connection ID
            collection_prefix: Prefix for MongoDB collections
            **kwargs: Additional CloudSourceAssociation fields

        Returns:
            The created CloudSourceAssociation, or None if failed
        """
        if not self._config:
            return None

        # Default collection prefix based on provider type
        if collection_prefix is None:
            collection_prefix = f"{provider_type.value}_"

        source = CloudSourceAssociation(
            connection_id=connection_id,
            provider_type=provider_type,
            display_name=display_name or f"{provider_type.value} connection",
            airbyte_source_id=airbyte_source_id,
            airbyte_connection_id=airbyte_connection_id,
            collection_prefix=collection_prefix,
            enabled=kwargs.get('enabled', True),
            sync_schedule=kwargs.get('sync_schedule'),
            include_paths=kwargs.get('include_paths', []),
            exclude_paths=kwargs.get('exclude_paths', []),
        )

        def edit(profile: ProfileConfig) -> Optional[CloudSourceAssociation]:
            if not profile.add_cloud_source(source):
                logger.error(f"Cloud source '{connection_id}' already exists in profile '{profile_key}'")
                return None
            return source

        added = self._edit_profile(profile_key, edit)
        if added:
            logger.info(f"Added cloud source '{connection_id}' to profile '{profile_key}'")
        return added

    def remove_cloud_source(self, profile_key: str, connection_id: str) -> bool:
        """
        Remove a cloud source connection from a profile.

        Args:
            profile_key: Profile to remove from
            connection_id: Connection ID to remove

        Returns:
            True if removed successfully
        """
        if not self._config:
            return False

        def edit(profile: ProfileConfig) -> bool:
            if not profile.remove_cloud_source(connection_id):
                logger.error(f"Cloud source '{connection_id}' not found in profile '{profile_key}'")
                return False
            return True

        if not self._edit_profile(profile_key, edit):
            return False
        logger.info(f"Removed cloud source '{connection_id}' from profile '{profile_key}'")
        return True

    def get_cloud_source(
        self,
        profile_key: str,
        connection_id: str
    ) -> Optional[CloudSourceAssociation]:
        """Get a specific cloud source from a profile."""
        profile = self.get_profile(profile_key)
        if not profile:
            return None

        return profile.get_cloud_source(connection_id)

    def list_cloud_sources(
        self,
        profile_key: Optional[str] = None,
        provider_type: Optional[CloudSourceType] = None
    ) -> List[CloudSourceAssociation]:
        """
        List cloud sources, optionally filtered by profile and/or type.

        Args:
            profile_key: If provided, only list sources for this profile
            provider_type: If provided, only list sources of this type

        Returns:
            List of matching cloud source associations (visible profiles only)
        """
        if not self._config:
            return []

        sources = []

        if profile_key:
            profile = self.get_profile(profile_key)
            if profile:
                if provider_type:
                    sources = profile.get_cloud_sources_by_type(provider_type)
                else:
                    sources = list(profile.cloud_sources)
        else:
            # List from all visible profiles
            for profile in self.list_profiles().values():
                if provider_type:
                    sources.extend(profile.get_cloud_sources_by_type(provider_type))
                else:
                    sources.extend(profile.cloud_sources)

        return sources

    def update_cloud_source(
        self,
        profile_key: str,
        connection_id: str,
        **updates
    ) -> bool:
        """
        Update a cloud source connection.

        Args:
            profile_key: Profile containing the cloud source
            connection_id: Connection ID to update
            **updates: Fields to update (display_name, enabled, sync_schedule, etc.)

        Returns:
            True if updated successfully
        """
        if not self._config:
            return False

        # Update allowed fields
        allowed_fields = [
            'display_name', 'enabled', 'sync_schedule',
            'airbyte_source_id', 'airbyte_connection_id',
            'last_sync_at', 'last_sync_status',
            'include_paths', 'exclude_paths', 'collection_prefix'
        ]

        def edit(profile: ProfileConfig) -> bool:
            source = profile.get_cloud_source(connection_id)
            if not source:
                logger.error(f"Cloud source '{connection_id}' not found in profile '{profile_key}'")
                return False
            for field, value in updates.items():
                if field in allowed_fields and value is not None:
                    setattr(source, field, value)
            return True

        if not self._edit_profile(profile_key, edit):
            return False
        logger.info(f"Updated cloud source '{connection_id}' in profile '{profile_key}'")
        return True

    # ==================== Airbyte Configuration ====================

    def set_airbyte_config(
        self,
        profile_key: str,
        workspace_id: Optional[str] = None,
        workspace_name: Optional[str] = None,
        destination_id: Optional[str] = None,
        default_sync_mode: Optional[str] = None,
        default_schedule_type: Optional[str] = None,
        default_schedule_cron: Optional[str] = None,
    ) -> bool:
        """
        Set or update Airbyte configuration for a profile.

        Args:
            profile_key: Profile to configure
            workspace_id: Airbyte workspace ID
            workspace_name: Airbyte workspace name
            destination_id: Airbyte MongoDB destination ID
            default_sync_mode: Default sync mode (full_refresh/incremental)
            default_schedule_type: Default schedule type (manual/scheduled)
            default_schedule_cron: Default cron expression

        Returns:
            True if configured successfully
        """
        if not self._config:
            return False

        def edit(profile: ProfileConfig) -> bool:
            # Create or update Airbyte config
            if profile.airbyte is None:
                profile.airbyte = AirbyteConfig()

            if workspace_id is not None:
                profile.airbyte.workspace_id = workspace_id
            if workspace_name is not None:
                profile.airbyte.workspace_name = workspace_name
            if destination_id is not None:
                profile.airbyte.destination_id = destination_id
            if default_sync_mode is not None:
                profile.airbyte.default_sync_mode = default_sync_mode
            if default_schedule_type is not None:
                profile.airbyte.default_schedule_type = default_schedule_type
            if default_schedule_cron is not None:
                profile.airbyte.default_schedule_cron = default_schedule_cron
            return True

        if not self._edit_profile(profile_key, edit):
            return False
        logger.info(f"Updated Airbyte config for profile '{profile_key}'")
        return True

    def get_airbyte_config(self, profile_key: str) -> Optional[AirbyteConfig]:
        """Get Airbyte configuration for a profile."""
        profile = self.get_profile(profile_key)
        if not profile:
            return None

        return profile.airbyte


# Global profile manager instance
_profile_manager: Optional[ProfileManager] = None


def get_profile_manager(profiles_path: Optional[str] = None, tenant_id: Optional[str] = None) -> ProfileManager:
    """
    Get or create the global profile manager instance.

    Args:
        profiles_path: Path to profiles.yaml. Only used on first call.
        tenant_id: Tenant to bind to on first call; defaults to the
            ``TENANT_ID`` environment variable (set for each tenant backend,
            not for the shared ingestion worker or the CLI).

    Returns:
        ProfileManager instance
    """
    global _profile_manager

    if _profile_manager is None:
        tenant = tenant_id if tenant_id is not None else os.getenv("TENANT_ID")
        _profile_manager = ProfileManager(profiles_path, tenant_id=tenant)

    return _profile_manager


def reset_profile_manager() -> None:
    """Reset the global profile manager (useful for testing)."""
    global _profile_manager
    _profile_manager = None


def get_active_profile() -> ProfileConfig:
    """Get the currently active profile configuration."""
    return get_profile_manager().active_profile
