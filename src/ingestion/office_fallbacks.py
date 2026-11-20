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

The audit of the 1,805 Word files that were still unreadable after those fixes
(``docs/26-FORMAT_EDGE_AUDIT.md``, follow-up 2026-11-19) added the readers for what the
file name hides: :func:`ole_container_kind` tells a Word binary from an encrypted package,
an Outlook message or a workbook under a ``.doc``/``.docx`` name; :func:`word2_binary_text`
reads Word 2.x for Windows files (no OLE container); :func:`docx_plain_text` reads the
paragraphs of a package Docling rejects; :func:`ole_embedded_images` finds the pictures of
a Word binary without body text; :func:`is_office_owner_content` recognises an owner
file that lost its ``~$`` prefix; :func:`ooxml_main_part_kind` tells a template package
from a document whatever its extension.
"""

from __future__ import annotations

import re
import struct
import zipfile
from typing import Dict, List, Optional, Tuple

__all__ = [
    "IMAGE_SUFFIXES",
    "OLE2_MAGIC",
    "WORD2_MAGIC",
    "WordBinaryError",
    "docx_plain_text",
    "has_backslash_member_names",
    "is_office_owner_content",
    "normalise_ooxml_member_names",
    "ole_container_kind",
    "ole_embedded_images",
    "ooxml_main_part_kind",
    "ooxml_media_images",
    "word2_binary_text",
    "word_binary_text",
]

_BACKSLASH = chr(92)
#: Picture formats inside an Office package that the image reader can OCR (not EMF/WMF vector art)
IMAGE_SUFFIXES: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif")
#: OLE2 compound file (Word 6-2003, Excel 97-2003, Outlook .msg, encrypted OOXML packages)
OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
#: Word 1.x/2.x for Windows: a bare FIB (``wIdent`` 0xA5DB), no OLE container
WORD2_MAGIC = b"\xdb\xa5"


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
            raise WordBinaryError("password-protected Word document")
        fc_min, fc_mac = struct.unpack_from("<II", word_document, 0x18)
        text: Optional[str] = None
        if version >= 193 and len(word_document) >= 0x01AA:  # Word 97 and later
            # The stories in CP order: main text, footnotes, headers/footers, macros,
            # comments, endnotes, text boxes, header text boxes
            stories = struct.unpack_from("<8I", word_document, 0x4C)
            ccp_text = stories[0]
            table_name = "1Table" if flags & 0x0200 else "0Table"
            if ole.exists(table_name):
                fc_clx, lcb_clx = struct.unpack_from("<II", word_document, 0x01A2)
                text = _piece_table_text(word_document, ole.openstream(table_name).read(), fc_clx, lcb_clx)
            if text is None:
                text = word_document[fc_min: fc_min + ccp_text].decode("cp1252", errors="replace")[:ccp_text]
            else:
                text = _main_or_other_stories(text, stories)
        else:  # Word 6/95: one run of 8-bit text from fcMin to fcMac
            if not 0 < fc_min <= fc_mac <= len(word_document):
                raise WordBinaryError("text offsets are outside the WordDocument stream")
            ccp_text = struct.unpack_from("<I", word_document, 0x34)[0]
            end = fc_min + ccp_text if 0 < ccp_text <= fc_mac - fc_min else fc_mac
            text = word_document[fc_min: end].decode("cp1252", errors="replace")
    return _clean_word_text(text)


def _main_or_other_stories(text: str, stories: Tuple[int, ...]) -> str:
    """The main story of a Word 97 piece-table text; the text boxes when it is empty.

    A form whose words sit in text boxes has no body text at all; its text is in the
    text-box story, after the footnotes, headers, macros, comments and endnotes. The
    header story is left out on purpose: for the 66 such files of one corpus it held
    "Seite 1 von 1" and letterheads, which would have passed as the document's text and
    kept the pictures of pasted scans from being OCRed (docs/26, follow-up 2026-11-19).
    """
    main = text[: stories[0]]
    if _clean_word_text(main):
        return main
    textbox_start = sum(stories[:6])
    return text[textbox_start: textbox_start + stories[6]]


def _clean_word_text(text: str) -> str:
    """Word story text as plain text: field results kept, marks turned into breaks, controls dropped."""
    text = _FIELD_CODE.sub("", text)
    text = text.replace("\x07", " | ").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    text = _CONTROL.sub("", text).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def word2_binary_text(path: str) -> str:
    """Body text of a Word 1.x/2.x for Windows file (:data:`WORD2_MAGIC`, no OLE container).

    ``antiword`` claims Word 2 support but returned nothing for the three such files of one
    corpus (1995/96 letters of the ``Pittner Archiv``) and rejects fast-saved ones; the text
    sniffer read them as text with a prefix of formatting bytes. The FIB of those files
    puts the text between ``fcMin`` (offset 0x18) and ``fcMac`` (0x1C) as 8-bit cp1252,
    field codes marked like in Word 6.

    Args:
        path: The ``.doc`` file.

    Returns:
        The text (may be empty).

    Raises:
        WordBinaryError: Not a Word 2 file, or its text offsets point outside the file.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:2] != WORD2_MAGIC or len(raw) < 0x40:
        raise WordBinaryError("not a Word 2 file")
    fc_min, fc_mac = struct.unpack_from("<II", raw, 0x18)
    if not 0 < fc_min <= fc_mac <= len(raw):
        raise WordBinaryError("text offsets are outside the file")
    # Word 2 ends a paragraph with CR LF (Word 6+ with a bare CR)
    return _clean_word_text(raw[fc_min:fc_mac].decode("cp1252", errors="replace").replace("\r\n", "\r"))


# ---------------------------------------------------------------------------
# What an OLE2 container holds
# ---------------------------------------------------------------------------

def ole_container_kind(path: str) -> Tuple[str, str]:
    """What an OLE2 compound file (:data:`OLE2_MAGIC`) under a Word name really is.

    The 1,805 unreadable Word files of one corpus held, besides Word binaries, Outlook
    messages, an Excel workbook, Office packages encrypted with a password (the
    ``EncryptedPackage`` stream), a Microsoft Works document and two truncated files
    (``docs/26-FORMAT_EDGE_AUDIT.md``). ``antiword`` names some of those in its error, but
    only after it ran; this looks at the stream names first.

    Args:
        path: The file.

    Returns:
        ``(kind, detail)`` with ``kind`` one of ``word``, ``word_encrypted``,
        ``encrypted_package``, ``msg``, ``excel``, ``powerpoint``, ``other`` (``detail``
        lists the top-level streams) or ``damaged`` (``detail`` is olefile's reason).
    """
    try:
        import olefile
    except ImportError:  # pragma: no cover - olefile ships with extract-msg
        return "word", ""
    try:
        container = olefile.OleFileIO(path)
    except Exception as exc:  # noqa: BLE001 - olefile raises several types for a damaged container
        return "damaged", f"{type(exc).__name__}: {exc}"
    with container as ole:
        names = ["/".join(entry) for entry in ole.listdir()]
        if ole.exists("EncryptedPackage"):
            return "encrypted_package", ""
        if ole.exists("WordDocument"):
            header = ole.openstream("WordDocument").read()
            if len(header) >= 0x0C and struct.unpack_from("<H", header, 0x0A)[0] & 0x0100:
                return "word_encrypted", ""
            return "word", ""
        if ole.exists("Workbook") or ole.exists("Book"):
            return "excel", ""
        if ole.exists("PowerPoint Document"):
            return "powerpoint", ""
        if any(name.startswith("__substg1.0_") or name == "__properties_version1.0" for name in names):
            return "msg", ""
        top_level = sorted({name.split("/")[0].lstrip("\x01\x03\x05\x06") for name in names})
        return "other", ", ".join(top_level[:6])


_JPEG_START = b"\xff\xd8\xff"
_JPEG_END = b"\xff\xd9"
_PNG_START = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"IEND\xaeB`\x82"


def ole_embedded_images(path: str, max_images: int = 30, min_bytes: int = 2048, max_bytes: int = 30 * 1024 * 1024) -> List[Tuple[str, bytes]]:
    """The JPEG and PNG pictures stored in a Word binary's OLE streams, in stream order.

    For a ``.doc`` without body text (scans and photos pasted into Word, faxes): the
    pictures of Word 97+ sit in the ``Data`` stream, those of older or fast-saved files in
    ``WordDocument``, embedded objects under ``ObjectPool``. The streams are scanned for the
    JPEG and PNG signatures (a picture's end is its end marker before the next picture), so
    metafiles and bitmaps are not found; the caller OCRs the result like the pictures of a
    ``.docx`` (:func:`ooxml_media_images`).

    Args:
        path: The ``.doc`` file.
        max_images: At most this many pictures are returned.
        min_bytes: Smaller blobs (icons, thumbnails) are skipped.
        max_bytes: Larger pictures are skipped.

    Returns:
        ``(name, bytes)`` pairs; the name carries the picture's format as its extension.
    """
    try:
        import olefile

        container = olefile.OleFileIO(path)
    except Exception:  # noqa: BLE001 - not an OLE2 file, or a damaged one
        return []
    images: List[Tuple[str, bytes]] = []
    with container as ole:
        for entry in ole.listdir():
            if len(images) >= max_images:
                break
            name = "/".join(entry)
            try:
                data = ole.openstream(name).read()
            except Exception:  # noqa: BLE001 - a damaged stream must not hide the others
                continue
            for blob, suffix in _picture_blobs(data):
                if min_bytes <= len(blob) <= max_bytes:
                    images.append((f"picture{len(images) + 1}{suffix}", blob))
                if len(images) >= max_images:
                    break
    return images


def _picture_blobs(data: bytes) -> List[Tuple[bytes, str]]:
    """JPEG/PNG blobs in ``data``, each cut from its signature to its own end marker."""
    starts = sorted(
        [(m.start(), ".jpg") for m in re.finditer(re.escape(_JPEG_START), data)]
        + [(m.start(), ".png") for m in re.finditer(re.escape(_PNG_START), data)]
    )
    blobs: List[Tuple[bytes, str]] = []
    for index, (start, suffix) in enumerate(starts):
        limit = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        marker = _JPEG_END if suffix == ".jpg" else _PNG_END
        end = data.rfind(marker, start, limit)
        if end < 0:
            continue
        blobs.append((data[start: end + len(marker)], suffix))
    return blobs


# ---------------------------------------------------------------------------
# Office owner files and OOXML packages
# ---------------------------------------------------------------------------

def is_office_owner_content(raw: bytes) -> bool:
    """Whether ``raw`` is the content of an Office owner file (``~$name.docx``) under another name.

    An owner file is 162 bytes: a length byte, the user name in the ANSI code page padded
    with zeros to 54 bytes, then the same name as UTF-16 (length, zero, characters). Two
    such files of one corpus had lost their ``~$`` prefix and were read as documents
    ("eggereggerMingLiUSimSun…"), see :func:`is_sidecar_file` in ``ingest.py`` for the
    named ones.
    """
    if len(raw) != 162:
        return False
    length = raw[0]
    if not 1 <= length <= 53:
        return False
    name = raw[1: 1 + length]
    if any(byte < 0x20 or byte == 0x7F for byte in name) or any(raw[1 + length: 54]):
        return False
    return raw[54] == length and raw[55] == 0 and raw[56: 56 + 2 * length] == name.decode("cp1252", "replace").encode("utf-16-le")


_MAIN_PART_KINDS: Tuple[Tuple[bytes, str], ...] = (
    (b"wordprocessingml.template.main+xml", ".dotx"),
    (b"wordprocessingml.document.main+xml", ".docx"),
    (b"spreadsheetml.template.main+xml", ".xltx"),
    (b"spreadsheetml.sheet.main+xml", ".xlsx"),
    (b"presentationml.template.main+xml", ".potx"),
    (b"presentationml.presentation.main+xml", ".pptx"),
)


def ooxml_main_part_kind(path: str) -> Optional[str]:
    """The extension that matches a package's main part: ``.docx``, ``.dotx``, ``.xlsx``, …

    Read from ``[Content_Types].xml``, not from the file name: a template saved as
    ``Brief.doc`` is still a template, and python-docx refuses it ("is not a Word file,
    content type is '…template.main+xml'") until the content type says document
    (``ooxml_template_as_document`` in ``ingest.py``).

    Returns:
        The extension, or ``None`` for a file that is not an Office package.
    """
    try:
        with zipfile.ZipFile(path) as package:
            member = next((info for info in package.infolist() if info.orig_filename == "[Content_Types].xml"), None)
            if member is None:
                return None
            content_types = package.read(member)
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    for marker, kind in _MAIN_PART_KINDS:
        if marker in content_types:
            return kind
    return None


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_plain_text(path: str) -> str:
    """The paragraphs of a Word package read straight from ``word/document.xml``.

    A fallback for packages Docling rejects for a reason that has nothing to do with the
    text (four WEKA templates of one corpus: a list definition with an empty ``w:start``
    value crashes its numbering code, "Pipeline SimplePipeline failed"). Paragraph order
    is kept, table rows become one line with `` | `` between the cells, tabs and line
    breaks are kept; styles, headers, footers, footnotes and pictures are not read.
    Member names with backslashes are accepted.

    Args:
        path: The package.

    Returns:
        The text; empty when the package has no ``word/document.xml`` or no text.
    """
    import xml.etree.ElementTree as ElementTree

    try:
        with zipfile.ZipFile(path) as package:
            member = next(
                (info for info in package.infolist() if info.orig_filename.replace(_BACKSLASH, "/") == "word/document.xml"),
                None,
            )
            if member is None:
                return ""
            root = ElementTree.fromstring(package.read(member))
    except (zipfile.BadZipFile, OSError, KeyError, ElementTree.ParseError):
        return ""
    body = root.find(_W + "body")
    if body is None:
        return ""
    lines: List[str] = []
    _collect_block_text(body, lines)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _paragraph_text(paragraph) -> str:  # noqa: ANN001 - an ElementTree element
    parts: List[str] = []
    for element in paragraph.iter():
        if element.tag == _W + "t":
            parts.append(element.text or "")
        elif element.tag == _W + "tab":
            parts.append("\t")
        elif element.tag in (_W + "br", _W + "cr"):
            parts.append("\n")
    return "".join(parts)


def _collect_block_text(container, lines: List[str]) -> None:  # noqa: ANN001 - an ElementTree element
    for child in container:
        if child.tag == _W + "p":
            lines.append(_paragraph_text(child))
        elif child.tag == _W + "tbl":
            for row in child.iter(_W + "tr"):
                cells = [" ".join(_paragraph_text(p) for p in cell.iter(_W + "p")).strip() for cell in row.findall(_W + "tc")]
                lines.append(" | ".join(cells))
        else:
            _collect_block_text(child, lines)
