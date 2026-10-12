"""File helpers that work with Windows paths longer than 260 characters.

Deep case-file trees exceed MAX_PATH easily. On Windows, paths of 240 or more
characters are passed to the OS with the extended-length prefix (backslash,
backslash, question mark, backslash), which works without changing the
system-wide ``LongPathsEnabled`` setting.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]
_PREFIX = "\\\\?\\"


def fs(p: PathLike) -> str:
    """OS path string, extended-length prefixed on Windows when needed."""
    s = os.path.abspath(str(p))
    if os.name == "nt" and len(s) >= 240 and not s.startswith(_PREFIX):
        return _PREFIX + s
    return s


def ensure_dir(p: PathLike) -> None:
    """Create a directory and its parents."""
    os.makedirs(fs(p), exist_ok=True)


def write_bytes(p: PathLike, data: bytes) -> None:
    """Write bytes, creating parent directories."""
    ensure_dir(Path(p).parent)
    with open(fs(p), "wb") as fh:
        fh.write(data)


def write_text(p: PathLike, text: str) -> None:
    """Write UTF-8 text, creating parent directories."""
    write_bytes(p, text.encode("utf-8"))


def read_bytes(p: PathLike) -> bytes:
    """Read a whole file."""
    with open(fs(p), "rb") as fh:
        return fh.read()


def is_file(p: PathLike) -> bool:
    """True if ``p`` is an existing file."""
    return os.path.isfile(fs(p))


def is_dir(p: PathLike) -> bool:
    """True if ``p`` is an existing directory."""
    return os.path.isdir(fs(p))


def copy2(src: PathLike, dst: PathLike) -> None:
    """Copy a file with metadata, creating parent directories."""
    ensure_dir(Path(dst).parent)
    shutil.copy2(fs(src), fs(dst))


def move(src: PathLike, dst: PathLike) -> None:
    """Move a file, creating parent directories."""
    ensure_dir(Path(dst).parent)
    shutil.move(fs(src), fs(dst))


def utime(p: PathLike, ts: float) -> None:
    """Set access and modification time."""
    os.utime(fs(p), (ts, ts))


def rmtree(p: PathLike) -> None:
    """Delete a directory tree if it exists."""
    shutil.rmtree(fs(p), ignore_errors=True)
