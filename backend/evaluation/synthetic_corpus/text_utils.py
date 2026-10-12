"""Formatting and matching helpers shared by planner, writer, renderers and validator."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_WS = re.compile(r"\s+")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def format_eur(amount: float | Decimal | str) -> str:
    """Austrian amount format: ``EUR 1.234,56``."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    integer, frac = f"{abs(value):.2f}".split(".")
    groups = []
    while len(integer) > 3:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    groups.insert(0, integer)
    return f"EUR {sign}{'.'.join(groups)},{frac}"


def format_date_de(iso: str) -> str:
    """``2025-03-12`` becomes ``12.03.2025``."""
    d = date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def normalize_for_match(text: str) -> str:
    """Whitespace- and dash-insensitive form used to check that a value appears in a text.

    NFKC folds non-breaking spaces and similar; soft hyphens and line-break
    hyphenation are removed; all whitespace collapses to one space.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "").replace("‑", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"-\s*\n\s*", "-", text)
    return _WS.sub(" ", text).strip()


def contains_value(text: str, value: str) -> bool:
    """True if ``value`` occurs in ``text`` modulo whitespace and dash variants."""
    return normalize_for_match(value) in normalize_for_match(text)


def _hint_tokens(hints: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß]{4,}", hints.lower())}


def find_quote(text: str, quote: str, context: int = 40, hints: str = "") -> tuple[str, str, str] | None:
    """Locate ``quote`` in ``text`` and return ``(quote, prefix, suffix)`` from the original text.

    Matching is whitespace-tolerant; the returned quote is the exact substring
    of ``text`` so selectors resolve against the canonical document text. If the
    value occurs several times (typical for dates), the occurrence whose
    surroundings share most words with ``hints`` (fact label and description)
    is chosen; ties go to the first occurrence.
    """
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    if not pattern:
        return None
    matches = list(re.finditer(pattern, text))
    if not matches:
        return None
    m = matches[0]
    tokens = _hint_tokens(hints)
    if len(matches) > 1 and tokens:
        def score(match: re.Match[str]) -> int:
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end != -1 else len(text)].lower()
            near = text[max(0, match.start() - 60):match.end() + 60].lower()
            return 2 * sum(1 for t in tokens if t in line) + sum(1 for t in tokens if t in near)

        m = max(matches, key=lambda x: (score(x), -x.start()))
    start, end = m.span()
    prefix = text[max(0, start - context):start]
    suffix = text[end:end + context]
    return text[start:end], prefix, suffix


def safe_filename(name: str, max_len: int = 120) -> str:
    """Make a Windows-safe file or folder name, keeping umlauts."""
    name = _INVALID_FILENAME.sub("-", name)
    name = _WS.sub(" ", name).strip(" .")
    return name[:max_len].rstrip(" .") or "Dokument"


def strip_markdown(text: str) -> str:
    """Plain text from the limited Markdown the writer produces (headings, bold, bullets)."""
    lines = []
    for line in text.splitlines():
        stripped = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        stripped = stripped.replace("**", "").replace("__", "")
        lines.append(stripped)
    return "\n".join(lines)
