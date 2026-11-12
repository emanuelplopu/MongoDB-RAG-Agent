"""Reader placeholders: the bracketed line a reader returns when a file has no usable text.

A reader that cannot produce text returns one bracketed line instead, for example
``[Error: Could not read file x.pdf]`` or ``[Image: logo.gif - OCR extracted minimal text]``.
A placeholder is never document content: the pipeline records the file as failed
(``"error"``) or as a file without text (``"empty"``) and stores nothing.

Until 2026-10-26 the ``[Error:`` placeholders were stored, chunked and embedded (F26 of the
prompt-test log); until 2026-11-11 the image, audio, video and empty-e-mail placeholders
still were: 8,600 ``[Image: … - OCR extracted minimal text]`` documents of one corpus were
embedded and competed with real documents in search (``docs/27-POST_INGESTION_VALIDATION.md``).

This module has no heavy imports, so the purge of stored placeholders
(``backend/services/placeholder_purge.py``) and the search path can share the table.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

__all__ = [
    "EMPTY_PLACEHOLDER_PREFIX",
    "EMPTY_PLACEHOLDER_PREFIXES",
    "ERROR_PLACEHOLDER_PREFIXES",
    "MEDIA_PLACEHOLDER_ERROR_MARKERS",
    "MEDIA_PLACEHOLDER_PREFIXES",
    "PLACEHOLDER_CONTENT_REGEX",
    "PLACEHOLDER_PREFIXES",
    "placeholder_kind",
]

#: Reader placeholders that mean "could not read" (an unreadable file, a missing tool)
ERROR_PLACEHOLDER_PREFIXES: Tuple[str, ...] = ("[Error:", "[Skipped:")
#: Reader placeholders that mean "read fine, but there is no text in it"
EMPTY_PLACEHOLDER_PREFIX = "[Document:"
#: The same, including e-mails without a body
EMPTY_PLACEHOLDER_PREFIXES: Tuple[str, ...] = (EMPTY_PLACEHOLDER_PREFIX, "[Empty email:")
#: Media placeholders (``[Image: logo.gif - OCR extracted minimal text]``): a file without
#: text unless the reason names a failure (:data:`MEDIA_PLACEHOLDER_ERROR_MARKERS`)
MEDIA_PLACEHOLDER_PREFIXES: Tuple[str, ...] = ("[Image:", "[Audio:", "[Video:")
MEDIA_PLACEHOLDER_ERROR_MARKERS: Tuple[str, ...] = ("OCR failed", "OCR not available")
#: Every prefix a placeholder can start with
PLACEHOLDER_PREFIXES: Tuple[str, ...] = (
    ERROR_PLACEHOLDER_PREFIXES + EMPTY_PLACEHOLDER_PREFIXES + MEDIA_PLACEHOLDER_PREFIXES
)

#: Anchored regular expression for a MongoDB pre-filter on ``content`` (candidates only:
#: :func:`placeholder_kind` decides, because real text may start with a bracketed word)
PLACEHOLDER_CONTENT_REGEX = r"^\s*\[(" + "|".join(re.escape(p[1:-1]) for p in PLACEHOLDER_PREFIXES) + r"):"

_PLACEHOLDER_MAX_CHARS = 400
#: An error placeholder carries the exception text, which can be long and span lines
_ERROR_PLACEHOLDER_MAX_CHARS = 2000


def placeholder_kind(content: Optional[str]) -> Optional[str]:
    """Classify a reader's output: ``"error"`` / ``"empty"`` for a placeholder, else ``None``.

    A placeholder is the whole output of a reader, in brackets; real document text never
    looks like that (a text that merely starts with ``[Error: …]`` and goes on is content).

    Args:
        content: The text a reader returned, or the stored content of a document.

    Returns:
        ``"error"`` for a failed read (the file is recorded as failed and can be retried),
        ``"empty"`` for a file without text (no chunks), ``None`` for real content.
    """
    text = (content or "").strip()
    if not text or not text.endswith("]"):
        return None
    is_error = text.startswith(ERROR_PLACEHOLDER_PREFIXES)
    is_media = text.startswith(MEDIA_PLACEHOLDER_PREFIXES)
    if is_error or (is_media and any(marker in text[:_PLACEHOLDER_MAX_CHARS] for marker in MEDIA_PLACEHOLDER_ERROR_MARKERS)):
        # "[Error: Could not read email x.msg: <exception text over several lines>]"
        return "error" if len(text) <= _ERROR_PLACEHOLDER_MAX_CHARS and "]\n" not in text else None
    if len(text) > _PLACEHOLDER_MAX_CHARS or "\n" in text:
        return None
    if is_media or text.startswith(EMPTY_PLACEHOLDER_PREFIXES):
        return "empty"
    return None
