"""Worker targets for the conversion-process tests.

Kept in a module of its own without heavy imports: the ``spawn`` child imports the module
that defines its target, and importing a test module would pull in pytest and the whole
ingestion pipeline.
"""

from __future__ import annotations

import os
import time
from typing import Any, Tuple


def sleepy_reader(file_path: str) -> Tuple[str, Any]:
    """Sleep for the number of seconds written in the file, then return it as the content.

    A file holding ``boom`` raises instead, like a reader that fails on a broken document.
    """
    with open(file_path, "r", encoding="utf-8") as handle:
        raw = handle.read().strip()
    if raw == "boom":
        raise ValueError(f"cannot read {os.path.basename(file_path)}")
    seconds = float(raw or 0)
    time.sleep(seconds)
    return f"slept {seconds:.1f} in {os.getpid()}", None


def noting_reader(file_path: str) -> Tuple[str, Any, Dict[str, Any]]:
    """Return the file's text plus reader notes, like ``read_document_in_worker`` does."""
    with open(file_path, "r", encoding="utf-8") as handle:
        raw = handle.read().strip()
    return raw, None, {"ocr_fallback": "full_page", "pid": os.getpid()}
