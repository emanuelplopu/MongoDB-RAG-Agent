"""Post-OCR text repair for Austrian legal documents.

Tesseract (the production OCR engine since ``OCR_ENGINE=tesseract``) reads the
section sign ``§`` of a statute reference as ``$`` (and ``§§`` as ``$$``), and
occasionally as the digit ``5`` (or ``8`` with the ``tessdata_best`` models):
``$ 94 ÄrzteG``, ``$$ RAO``, ``5 10 AVG``, ``$ 5 Abs 2 KSchG``, ``8 65 Zi 1 lit c StPO``. For a legal corpus that is the one systematic OCR error
that breaks exact-value and BM25 matching of the facts people search for, so
it is repaired at ingestion time, on the converted text and on the Docling
document the chunker works from.

The repair is deliberately narrow: a ``$``/``$$``/``5`` is turned into
``§``/``§§`` only when it is followed by a section number and then by
something only a statute reference has (``Abs``, ``Z``, ``lit``, ``ff``, a
known law abbreviation or a word ending in ``-gesetz``/``-ordnung``). Amounts
(``$ 5.000,00``), ``US$ 10 Mio`` and plain numbers are left alone.

Because the stored ``content`` and the chunks are the OCR text, the repair
only affects documents ingested (or re-ingested) after it was added.
"""

from __future__ import annotations

import re
from typing import Any, Tuple

# Abbreviations of Austrian (and a few EU) statutes seen in a law firm's files.
# Word-bounded, case-sensitive; extend as new ones show up in the harness output.
LAW_ABBREVIATIONS: Tuple[str, ...] = (
    "ABGB", "AktG", "AngG", "ArbVG", "ASGG", "ASVG", "AußStrG", "AVG", "AVRAG", "AZG", "AÜG",
    "BAO", "BDG", "BEinstG", "B-VG", "DSG", "DSGVO", "EFZG", "EheG", "EO", "EStG", "FinStrG",
    "GBG", "GebAG", "GewO", "GlBG", "GmbHG", "GOG", "IO", "JN", "KFG", "KO", "KSchG", "KStG",
    "MRG", "MSchG", "NO", "RAO", "RATG", "RL", "RPflG", "StGB", "StPO", "StVO", "TKG", "UGB",
    "UrhG", "UrlG", "UStG", "VStG", "VwGG", "VwGVG", "WEG", "WGG", "WKAG", "ZaDiG", "ZPO",
    "ÄrzteG", "ArzteG",  # the umlaut is sometimes lost by the OCR as well
)

_LAW = "|".join(re.escape(a) for a in sorted(LAW_ABBREVIATIONS, key=len, reverse=True))
# What may follow the section number inside one reference: "Abs 2", "Abs. 1 Z 3 lit c", "ff", "iVm", ...
_TAIL = r"(?:\s?(?:Abs\.?|Zi(?:ff)?\.?|Z\.?|lit\.?|ff\.?|iVm|Satz)\s?\d{0,3}[a-z]?\.?){0,4}"
# The statute itself: an abbreviation, or a spelled-out "...gesetz" / "...ordnung" (optionally "des österreichischen ...")
_STATUTE = rf"(?:(?:des|der|dem)\s(?:\w+\s){{0,2}})?(?:(?:{_LAW})\b|\w+(?:gesetz|gesetzes|ordnung)\b)"

# "$ 94 ÄrzteG", "$$ 8 ff RAO", "$ 7 Abs.3 des österreichischen Patentgesetzes", "$ 5 Abs 2 KSchG]"
_DOLLAR_RE = re.compile(
    rf"(?<![\w€])(?P<sign>\$\$?)\s?(?P<num>\d{{1,4}}[a-z]?)(?P<tail>{_TAIL})(?P<sep>\s?)(?P<law>{_STATUTE})"
)
# "$ 7 Abs.3 des ..." where the statute is not recognisable: "Abs"/"Z"/"lit" after the number is proof enough
_DOLLAR_ABS_RE = re.compile(r"(?<![\w€])(?P<sign>\$\$?)\s?(?P<num>\d{1,4}[a-z]?)\s?(?=(?:Abs\.?|Zi(?:ff)?\.?|Z\.?|lit\.?)\s?\d)")
# "$$ RAO" (no number: "gemäß §§ RAO")
_DOLLAR_LAW_RE = re.compile(rf"(?<![\w€])\$\$\s?(?=(?:{_LAW})\b)")
# "5 10 AVG", "8 65 Zi 1 lit c StPO" (the tessdata_best models read § as 8): a lone "5"/"8", a space,
# the section number, then Abs/Z/lit or a statute. Never "52 Abs" (no space).
_FIVE_RE = re.compile(
    rf"(?<![\w,.\-/§$])[58]\s(?P<num>\d{{1,4}}[a-z]?)(?P<tail>{_TAIL})(?P<sep>\s?)(?=(?:Abs\.?|Zi(?:ff)?\.?|Z\.?|lit\.?)\s?\d|(?:{_STATUTE}))"
)


def _dollar_sub(m: re.Match[str]) -> str:
    sign = "§§" if m.group("sign") == "$$" else "§"
    return f"{sign} {m.group('num')}{m.group('tail')}{m.group('sep')}{m.group('law')}"


def _dollar_abs_sub(m: re.Match[str]) -> str:
    sign = "§§" if m.group("sign") == "$$" else "§"
    return f"{sign} {m.group('num')} "


def _five_sub(m: re.Match[str]) -> str:
    return f"§ {m.group('num')}{m.group('tail')}{m.group('sep')}"


def repair_ocr_text(text: str) -> str:
    """Repair OCR misreads of ``§`` in statute references.

    Args:
        text: Converted document text (Markdown or plain).

    Returns:
        The text with ``$``/``$$``/``5`` restored to ``§``/``§§`` where a statute
        reference follows; unchanged otherwise.
    """
    if not text or ("$" not in text and "5 " not in text and "8 " not in text):
        return text
    out = _DOLLAR_RE.sub(_dollar_sub, text)
    out = _DOLLAR_ABS_RE.sub(_dollar_abs_sub, out)
    out = _DOLLAR_LAW_RE.sub("§§ ", out)
    out = _FIVE_RE.sub(_five_sub, out)
    return out


def repair_docling_document(document: Any) -> int:
    """Apply :func:`repair_ocr_text` to every text item and table cell of a DoclingDocument in place.

    The HybridChunker builds chunk text from the document items, not from the
    exported Markdown, so the document itself has to be repaired for the chunks
    (and their embeddings) to carry the corrected references.

    Args:
        document: A ``DoclingDocument`` (or ``None``).

    Returns:
        Number of items whose text changed.
    """
    if document is None:
        return 0
    changed = 0
    for item in getattr(document, "texts", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            fixed = repair_ocr_text(text)
            if fixed != text:
                item.text = fixed
                changed += 1
    for table in getattr(document, "tables", None) or []:
        data = getattr(table, "data", None)
        for cell in getattr(data, "table_cells", None) or []:
            text = getattr(cell, "text", None)
            if isinstance(text, str):
                fixed = repair_ocr_text(text)
                if fixed != text:
                    cell.text = fixed
                    changed += 1
    return changed
