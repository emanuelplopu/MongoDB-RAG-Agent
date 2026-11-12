"""Fallback readers for Office files the main readers reject.

Three kinds of Word files of one law firm's shares failed although Word opens them
(``docs/27-POST_INGESTION_VALIDATION.md``):

* **OOXML packages whose member names use backslashes** (``word\\document.xml``). The
  firm's practice-management software writes its letters, fee notes and cost schedules
  that way; a zip reader on Linux finds no ``_rels/.rels`` and Docling answers
  "MsWordDocumentBackend could not load document". About 500 documents.
  :func:`normalise_ooxml_member_names` writes a copy with forward slashes.
* **Small Word 6/95 files.** ``antiword`` gives up with "the text stream of this file is
  too small to handle" when the document's text lives in the OLE mini stream (files of a
  few kilobytes: cost schedules, specimen signatures, resolutions of the 1990s). About
  490 documents. :func:`word_binary_text` reads the text from the ``WordDocument`` stream.
* **Word files that hold only pictures** (photographed exhibits, tickets, screenshots
  pasted into a document). The converter returns picture placeholders and no text; 229
  documents. :func:`ooxml_media_images` hands the pictures to the caller for OCR.
"""

from __future__ import annotations

import re
import struct
import zipfile
from typing import List, Optional, Tuple

__all__ = [
    "IMAGE_SUFFIXES",
    "WordBinaryError",
    "has_backslash_member_names",
    "normalise_ooxml_member_names",
    "ooxml_media_images",
    "word_binary_text",
]

_BACKSLASH = chr(92)
#: Picture formats inside an Office package that the image reader can OCR (not EMF/WMF vector art)
IMAGE_SUFFIXES: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif")


class WordBinaryError(ValueError):
    """The file is not a readable Word binary document (the message says why)."""


# ---------------------------------------------------------------------------
# OOXML packages
# ---------------------------------------------------------------------------

def has_backslash_member_names(path: str) -> bool:
    """Whether a zip package (``.docx``, ``.xlsx``, ``.pptx``) names its parts with backslashes.

    Only the central directory is read. A file that is not a zip returns ``False``. The
    names are taken as stored (``orig_filename``): on Windows ``zipfile`` converts
    backslashes on reading, which is why the same files open there.
    """
    try:
        with zipfile.ZipFile(path) as package:
            return any(_BACKSLASH in info.orig_filename for info in package.infolist())
    except (zipfile.BadZipFile, OSError):
        return False


def normalise_ooxml_member_names(src: str, dst: str) -> int:
    """Write the package ``src`` to ``dst`` with forward slashes in every member name.

    Args:
        src: An OOXML package whose member names use backslashes.
        dst: Destination path.

    Returns:
        The number of members renamed.
    """
    renamed = 0
    with zipfile.ZipFile(src) as package, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        seen = set()
        for info in package.infolist():
            name = info.orig_filename.replace(_BACKSLASH, "/")
            if name in seen:
                continue
            seen.add(name)
            renamed += name != info.orig_filename
            out.writestr(name, package.read(info))
    return renamed


def ooxml_media_images(path: str, max_images: int = 30, max_bytes: int = 30 * 1024 * 1024) -> List[Tuple[str, bytes]]:
    """The pictures embedded in an Office package, in document order of their file names.

    Args:
        path: A ``.docx`` / ``.pptx`` / ``.xlsx`` package.
        max_images: At most this many pictures are returned.
        max_bytes: Pictures larger than this are skipped.

    Returns:
        ``(member name, bytes)`` of the raster pictures under ``*/media/``.
    """
    images: List[Tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(path) as package:
            members = [
                (info.orig_filename.replace(_BACKSLASH, "/"), info)
                for info in package.infolist()
                if "/media/" in info.orig_filename.replace(_BACKSLASH, "/") and info.orig_filename.lower().endswith(IMAGE_SUFFIXES)
            ]

            def order(member: Tuple[str, zipfile.ZipInfo]) -> Tuple[int, str]:
                number = re.search(r"(\d+)\.[a-z]+$", member[0].lower())
                return (int(number.group(1)) if number else 0, member[0])

            for name, info in sorted(members, key=order)[:max_images]:
                if info.file_size <= max_bytes:
                    images.append((name.rsplit("/", 1)[-1], package.read(info)))
    except (zipfile.BadZipFile, OSError, KeyError):
        return []
    return images


# ---------------------------------------------------------------------------
# Word binary (.doc)
# ---------------------------------------------------------------------------

_FIELD_CODE = re.compile(r"\x13[^\x14\x15]*\x14")  # field begin .. separator: the instruction, not the result
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f]")


def _piece_table_text(word_document: bytes, table: bytes, fc_clx: int, lcb_clx: int) -> Optional[str]:
    """Text of a Word 97+ document from its piece table, ``None`` when the table is not readable."""
    clx = table[fc_clx: fc_clx + lcb_clx]
    pos = 0
    while pos + 3 <= len(clx) and clx[pos] == 1:  # property modifiers precede the piece table
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos + 5 > len(clx) or clx[pos] != 2:
        return None
    lcb = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5: pos + 5 + lcb]
    pieces = (lcb - 4) // 12
    if pieces <= 0 or len(plc) < 4 * (pieces + 1) + 8 * pieces:
        return None
    positions = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    parts: List[str] = []
    for i in range(pieces):
        fc = struct.unpack_from("<I", plc, 4 * (pieces + 1) + 8 * i + 2)[0]
        count = positions[i + 1] - positions[i]
        if count <= 0:
            continue
        if fc & 0x40000000:  # 8-bit text (cp1252), stored at half the offset
            offset = (fc & 0x3FFFFFFF) // 2
            parts.append(word_document[offset: offset + count].decode("cp1252", errors="replace"))
        else:
            parts.append(word_document[fc: fc + 2 * count].decode("utf-16-le", errors="replace"))
    return "".join(parts)


def word_binary_text(path: str) -> str:
    """Body text of a Word 6/95/97-2003 ``.doc`` file, read from its ``WordDocument`` stream.

    A fallback for the files ``antiword`` rejects (small documents whose streams live in
    the OLE mini stream). Paragraph marks become line breaks, table cell marks `` | ``;
    field instructions are dropped, their results kept. Formatting, headers, footers and
    footnotes are not extracted.

    Args:
        path: The ``.doc`` file.

    Returns:
        The text (may be empty for a document without body text).

    Raises:
        WordBinaryError: Not an OLE2 Word document, encrypted, or a truncated header.
    """
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - olefile ships with extract-msg
        raise WordBinaryError("olefile is not installed") from exc
    if not olefile.isOleFile(path):
        raise WordBinaryError("not an OLE2 file")
    try:
        container = olefile.OleFileIO(path)
    except Exception as exc:  # noqa: BLE001 - olefile raises several types for a damaged container
        raise WordBinaryError(f"damaged OLE2 container ({type(exc).__name__})") from exc
    with container as ole:
        if not ole.exists("WordDocument"):
            raise WordBinaryError("no WordDocument stream")
        word_document = ole.openstream("WordDocument").read()
        if len(word_document) < 0x60:
            raise WordBinaryError("WordDocument stream is too short")
        _ident, version = struct.unpack_from("<HH", word_document, 0)
        flags = struct.unpack_from("<H", word_document, 0x0A)[0]
        if flags & 0x0100:
            raise WordBinaryError("the document is encrypted")
        fc_min, fc_mac = struct.unpack_from("<II", word_document, 0x18)
        text: Optional[str] = None
        if version >= 193 and len(word_document) >= 0x01AA:  # Word 97 and later
            ccp_text = struct.unpack_from("<I", word_document, 0x4C)[0]
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if ole.exists(table_name):
                fc_clx, lcb_clx = struct.unpack_from("<II", word_document, 0x01A2)
                text = _piece_table_text(word_document, ole.openstream(table_name).read(), fc_clx, lcb_clx)
            if text is None:
                text = word_document[fc_min: fc_min + ccp_text].decode("cp1252", errors="replace")
            text = text[:ccp_text]
        else:  # Word 6/95: one run of 8-bit text from fcMin to fcMac
            if not 0 < fc_min <= fc_mac <= len(word_document):
                raise WordBinaryError("text offsets are outside the WordDocument stream")
            ccp_text = struct.unpack_from("<I", word_document, 0x34)[0]
            end = fc_min + ccp_text if 0 < ccp_text <= fc_mac - fc_min else fc_mac
            text = word_document[fc_min: end].decode("cp1252", errors="replace")
    text = _FIELD_CODE.sub("", text)
    text = text.replace("\x07", " | ").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    text = _CONTROL.sub("", text).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
