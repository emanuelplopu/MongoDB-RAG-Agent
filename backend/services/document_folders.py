"""Document folder roots: validation and browsing for profile folders.

Profiles list the folders the ingestion worker scans (``documents_folders``
in ``profiles.yaml``). Those paths are container paths: local folders are
bind-mounted below ``/app/mounts`` and network shares are attached there as
CIFS volumes (``docker-compose.shares.yml``). Organisation admins pick their
folders through the Profiles page, so this module makes sure they can only
choose directories below the configured document roots
(``DOCUMENT_ROOTS`` in :mod:`backend.core.config`) and that the directory is
actually reachable from the API container, which shares every mount with
the worker. Superadmins keep editing arbitrary paths.

The functions here do blocking filesystem work (network shares can be slow);
routers call them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional

from backend.models.schemas import FolderBrowseResponse, FolderEntry

#: Directory names never offered in the folder picker.
_HIDDEN_DIRECTORY_NAMES = frozenset(
    {"$RECYCLE.BIN", "System Volume Information", "lost+found", "@eaDir", "#recycle"}
)

#: Upper bound for one directory listing, so a huge share cannot stall the UI.
MAX_BROWSE_ENTRIES = 1000


class FolderNotAllowedError(ValueError):
    """The path is outside every configured document root (or malformed)."""


class FolderNotFoundError(ValueError):
    """The path does not exist or is not a directory on this server."""


def normalize_folder(path: str) -> str:
    """Return an absolute, normalised form of ``path`` without resolving symlinks.

    Symlinks are left alone on purpose: ``/app/mounts/<name>`` is what the
    profile stores and what the worker scans, whatever it points to.
    """
    return os.path.normpath(os.path.abspath(path))


def _is_within(path: str, root: str) -> bool:
    """True when ``path`` equals ``root`` or lies below it (both normalised)."""
    if path == root:
        return True
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(prefix)


def find_root(path: str, roots: Iterable[str]) -> Optional[str]:
    """Return the normalised root that contains ``path``, or ``None``."""
    normalized = normalize_folder(path)
    for root in roots:
        if _is_within(normalized, normalize_folder(root)):
            return normalize_folder(root)
    return None


def assert_folders_allowed(folders: Iterable[str], roots: List[str]) -> List[str]:
    """Validate folders an organisation admin wants to store on a profile.

    Args:
        folders: Paths as entered or picked in the UI.
        roots: Allowed document roots (``settings.get_document_roots()``).

    Returns:
        The normalised folder paths, de-duplicated, in the given order.

    Raises:
        FolderNotAllowedError: A path is relative, empty, or outside every root,
            or no roots are configured at all.
        FolderNotFoundError: A path is not an existing directory on this server.
    """
    if not roots:
        raise FolderNotAllowedError(
            "No document roots are configured on this server (DOCUMENT_ROOTS); "
            "ask the administrator to attach the folder first"
        )
    accepted: List[str] = []
    for raw in folders:
        candidate = (raw or "").strip()
        if not candidate:
            raise FolderNotAllowedError("Folder paths must not be empty")
        if not os.path.isabs(candidate):
            raise FolderNotAllowedError(
                f"Folder '{candidate}' must be an absolute path below one of: {', '.join(roots)}"
            )
        if find_root(candidate, roots) is None:
            raise FolderNotAllowedError(
                f"Folder '{candidate}' is outside the document roots ({', '.join(roots)}). "
                "Network shares and local folders have to be attached by the administrator first"
            )
        normalized = normalize_folder(candidate)
        if not os.path.isdir(normalized):
            raise FolderNotFoundError(
                f"Folder '{candidate}' is not reachable from the server. "
                "Check that the share or mount is attached and the path is spelled correctly"
            )
        if normalized not in accepted:
            accepted.append(normalized)
    return accepted


def _is_hidden(name: str) -> bool:
    return name.startswith(".") or name in _HIDDEN_DIRECTORY_NAMES


def browse_folders(path: Optional[str], roots: List[str]) -> FolderBrowseResponse:
    """List the document roots, or the sub-folders of one directory below a root.

    Args:
        path: ``None``/blank lists the roots themselves; otherwise a directory
            below one of the roots whose immediate sub-directories are returned.
        roots: Allowed document roots.

    Returns:
        The listing. Hidden and system directories are skipped and at most
        :data:`MAX_BROWSE_ENTRIES` entries are returned (``truncated`` tells).

    Raises:
        FolderNotAllowedError: ``path`` is outside every root.
        FolderNotFoundError: ``path`` is not an existing, readable directory.
    """
    normalized_roots = [normalize_folder(r) for r in roots]
    if not path or not path.strip():
        entries = [
            FolderEntry(name=root, path=root, exists=os.path.isdir(root))
            for root in normalized_roots
        ]
        return FolderBrowseResponse(roots=normalized_roots, path=None, parent=None, entries=entries)

    root = find_root(path, roots)
    if root is None:
        raise FolderNotAllowedError(
            f"Folder '{path}' is outside the document roots ({', '.join(roots)})"
        )
    target = normalize_folder(path)
    if not os.path.isdir(target):
        raise FolderNotFoundError(f"Folder '{path}' is not reachable from the server")

    entries: List[FolderEntry] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for entry in it:
                if _is_hidden(entry.name):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                entries.append(FolderEntry(name=entry.name, path=os.path.join(target, entry.name), exists=True))
                if len(entries) >= MAX_BROWSE_ENTRIES:
                    truncated = True
                    break
    except PermissionError as exc:
        raise FolderNotFoundError(f"Folder '{path}' is not readable by the server: {exc}") from exc
    except OSError as exc:
        raise FolderNotFoundError(f"Folder '{path}' could not be listed: {exc}") from exc

    entries.sort(key=lambda e: e.name.casefold())
    parent = None if target == root else os.path.dirname(target)
    return FolderBrowseResponse(
        roots=normalized_roots, path=target, parent=parent, entries=entries, truncated=truncated
    )
