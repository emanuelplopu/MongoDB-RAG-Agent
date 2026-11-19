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

Two kinds of PowerPoint files followed (``docs/26-FORMAT_EDGE_AUDIT.md``, PowerPoint
follow-up of 2026-11-19):

* **PowerPoint 97-2003 ``.ppt`` decks.** Docling reads ``.pptx`` only, and the image has
  no LibreOffice. :func:`ppt_binary_text` walks the ``PowerPoint Document`` record stream
  for its text atoms; :func:`ppt_binary_pictures` hands the pictures of the ``Pictures``
  stream to the caller for OCR (photo decks).
* **Packages cut off by an interrupted copy** (no central directory, so neither ``zipfile``
  nor Docling opens them, while every part before the cut is intact).
  :func:`truncated_ooxml_text` reads the slide or body parts from the local file headers.
"""

from __future__ import annotations

import re
import struct
import zipfile
from typing import List, Optional, Tuple

__all__ = [
    "IMAGE_SUFFIXES",
    "PptBinaryError",
    "TruncatedPackageError",
    "WordBinaryError",
    "has_backslash_member_names",
    "normalise_ooxml_member_names",
    "ooxml_media_images",
    "ppt_binary_pictures",
    "ppt_binary_text",
    "truncated_ooxml_text",
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


# ---------------------------------------------------------------------------
# PowerPoint binary (.ppt)
# ---------------------------------------------------------------------------

class PptBinaryError(ValueError):
    """The file is not a readable PowerPoint 97-2003 presentation (the message says why)."""


_RT_DOCUMENT = 0x03E8
_RT_SLIDE = 0x03EE
_RT_NOTES = 0x03F0
_RT_MAIN_MASTER = 0x03F8
_RT_SLIDE_PERSIST_ATOM = 0x03F3
_RT_TEXT_CHARS_ATOM = 0x0FA0
_RT_TEXT_BYTES_ATOM = 0x0FA8
_RT_SLIDE_LIST_WITH_TEXT = 0x0FF0
_RT_USER_EDIT_ATOM = 0x0FF5
_RT_PERSIST_DIRECTORY_ATOM = 0x1772
_RT_CRYPT_SESSION = 0x2F14
_SLIDE_LIST_MASTERS = 1  # recInstance of the masters' SlideListWithText
_CURRENT_USER_ENCRYPTED = 0xF3D1C4DF
_PRIVATE_USE = re.compile("[-]")  # symbol-font bullets (Wingdings) in text runs
_PPT_CONTROL = re.compile("[\x00-\x08\x0e-\x1f\x7f]")

#: OfficeArt picture records of the ``Pictures`` stream that the image reader can OCR:
#: record type -> (file suffix, the recInstance values that carry a second 16-byte UID)
_BLIP_TYPES = {
    0xF01D: (".jpg", (0x46B, 0x6E3)),
    0xF01E: (".png", (0x6E1,)),
    0xF029: (".tif", (0x6E5,)),
}


def _records(buf: bytes, start: int, end: int):
    """Yield ``(offset, instance, version, type, body_start, body_end)`` of the records in ``buf[start:end]``."""
    off = start
    while off + 8 <= end:
        verinst, rtype, rlen = struct.unpack_from("<HHI", buf, off)
        body = off + 8
        yield off, verinst >> 4, verinst & 0xF, rtype, body, min(body + rlen, end)
        off = body + rlen


def _text_atoms(buf: bytes, start: int, end: int, skip_master_lists: bool) -> List[str]:
    """Text of every TextCharsAtom / TextBytesAtom below ``buf[start:end]``, in stream order."""
    texts: List[str] = []
    for _off, instance, version, rtype, body, body_end in _records(buf, start, end):
        if version == 0xF:
            if skip_master_lists and rtype == _RT_SLIDE_LIST_WITH_TEXT and instance == _SLIDE_LIST_MASTERS:
                continue
            texts.extend(_text_atoms(buf, body, body_end, skip_master_lists))
        elif rtype == _RT_TEXT_CHARS_ATOM:
            texts.append(buf[body:body_end].decode("utf-16-le", errors="replace"))
        elif rtype == _RT_TEXT_BYTES_ATOM:
            texts.append(buf[body:body_end].decode("cp1252", errors="replace"))
    return texts


def _persist_directory(ole, stream: bytes) -> Optional[Tuple[dict, int]]:
    """``(persist id -> stream offset, persist id of the document)`` of the current edit.

    PowerPoint saved incrementally: an edit appends new copies of the changed slides and a
    new ``UserEditAtom``; the ``Current User`` stream points at the latest one, and the chain
    of persist directories (latest first) says which copy of each object is current.

    Returns:
        The directory, or ``None`` when the streams do not resolve.

    Raises:
        PptBinaryError: The ``Current User`` stream marks the presentation as encrypted.
    """
    if not ole.exists("Current User"):
        return None
    current_user = ole.openstream("Current User").read()
    if len(current_user) < 20:
        return None
    header_token, offset_to_edit = struct.unpack_from("<II", current_user, 12)
    if header_token == _CURRENT_USER_ENCRYPTED:
        raise PptBinaryError("the presentation is encrypted")
    offsets: dict = {}
    seen = set()
    document_id: Optional[int] = None
    while offset_to_edit and offset_to_edit not in seen and offset_to_edit + 8 + 28 <= len(stream):
        seen.add(offset_to_edit)
        _verinst, rtype, _rlen = struct.unpack_from("<HHI", stream, offset_to_edit)
        if rtype != _RT_USER_EDIT_ATOM:
            return None
        offset_last_edit, offset_directory, doc_persist_id = struct.unpack_from("<III", stream, offset_to_edit + 8 + 8)
        if document_id is None:
            document_id = doc_persist_id
        if offset_directory + 8 > len(stream):
            return None
        _verinst, rtype, rlen = struct.unpack_from("<HHI", stream, offset_directory)
        if rtype != _RT_PERSIST_DIRECTORY_ATOM:
            return None
        pos, end = offset_directory + 8, min(offset_directory + 8 + rlen, len(stream))
        while pos + 4 <= end:
            entry = struct.unpack_from("<I", stream, pos)[0]
            first_id, count = entry & 0xFFFFF, entry >> 20
            pos += 4
            for i in range(count):
                if pos + 4 > end:
                    break
                offsets.setdefault(first_id + i, struct.unpack_from("<I", stream, pos)[0])
                pos += 4
        offset_to_edit = offset_last_edit
    if not offsets or document_id is None:
        return None
    return offsets, document_id


def _referenced_objects(stream: bytes, directory: Tuple[dict, int]) -> Optional[set]:
    """Offsets of the document and of the slides and notes its slide lists reference.

    Title masters, the notes master and the handout master are slide containers too, but
    only the masters' list (``recInstance`` 1) points at them, so they stay out.

    Returns:
        The offsets, or ``None`` when the document container is not where the directory says.
    """
    offsets, document_id = directory
    document_offset = offsets.get(document_id)
    if document_offset is None or document_offset + 8 > len(stream):
        return None
    verinst, rtype, rlen = struct.unpack_from("<HHI", stream, document_offset)
    if rtype != _RT_DOCUMENT or verinst & 0xF != 0xF:
        return None
    referenced = {document_offset}
    body, body_end = document_offset + 8, min(document_offset + 8 + rlen, len(stream))
    for _off, instance, version, rtype, list_body, list_end in _records(stream, body, body_end):
        if version != 0xF or rtype != _RT_SLIDE_LIST_WITH_TEXT or instance == _SLIDE_LIST_MASTERS:
            continue
        for _o, _i, _v, atom_type, atom_body, atom_end in _records(stream, list_body, list_end):
            if atom_type == _RT_SLIDE_PERSIST_ATOM and atom_end - atom_body >= 4:
                offset = offsets.get(struct.unpack_from("<I", stream, atom_body)[0])
                if offset is not None:
                    referenced.add(offset)
    return referenced


def _clean_ppt_text(text: str) -> str:
    text = text.replace("\r", "\n").replace("\x0b", "\n").replace("\t", " ")
    text = _PRIVATE_USE.sub("", text)
    text = _PPT_CONTROL.sub("", text).replace("\xa0", " ")
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    return text if any(ch.isalnum() for ch in text) else ""  # "*" of a slide-number placeholder


def ppt_binary_text(path: str) -> str:
    """Text of a PowerPoint 97-2003 ``.ppt`` file, read from its ``PowerPoint Document`` stream.

    Docling reads ``.pptx`` only. The record stream is walked for the text atoms of the
    document's slide lists (placeholder text) and of the slide and notes containers (text
    boxes), in stream order. When the ``Current User`` stream and the persist directories
    resolve, only the document and the slides and notes its slide lists reference are read:
    a presentation saved incrementally does not repeat its slides, and the masters (whose
    "Click to edit Master title style" placeholders are slide containers too) stay out.
    Without a directory every slide and notes container is read. Paragraph and line breaks
    become line breaks; symbol-font bullets and bare slide-number placeholders are dropped.

    Args:
        path: The ``.ppt`` file.

    Returns:
        The text, one blank line between text runs (may be empty for a picture-only deck).

    Raises:
        PptBinaryError: Not an OLE2 PowerPoint file, encrypted, or no document stream.
    """
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - olefile ships with extract-msg
        raise PptBinaryError("olefile is not installed") from exc
    if not olefile.isOleFile(path):
        raise PptBinaryError("not an OLE2 file")
    try:
        container = olefile.OleFileIO(path)
    except Exception as exc:  # noqa: BLE001 - olefile raises several types for a damaged container
        raise PptBinaryError(f"damaged OLE2 container ({type(exc).__name__})") from exc
    with container as ole:
        if not ole.exists("PowerPoint Document"):
            raise PptBinaryError("no PowerPoint Document stream")
        stream = ole.openstream("PowerPoint Document").read()
        directory = _persist_directory(ole, stream)
        referenced = _referenced_objects(stream, directory) if directory else None
        texts: List[str] = []
        for off, _instance, version, rtype, body, body_end in _records(stream, 0, len(stream)):
            if rtype == _RT_CRYPT_SESSION:
                raise PptBinaryError("the presentation is encrypted")
            if version != 0xF or rtype not in (_RT_DOCUMENT, _RT_SLIDE, _RT_NOTES):
                continue
            if referenced is not None and off not in referenced:
                continue  # a master, or an earlier copy of an object that a later edit replaced
            texts.extend(_text_atoms(stream, body, body_end, skip_master_lists=rtype == _RT_DOCUMENT))
    cleaned = [t for t in (_clean_ppt_text(t) for t in texts) if t]
    return "\n\n".join(cleaned)


def ppt_binary_pictures(path: str, max_images: int = 30, max_bytes: int = 30 * 1024 * 1024) -> List[Tuple[str, bytes]]:
    """The raster pictures of a ``.ppt`` file's ``Pictures`` stream, in stream order.

    Args:
        path: The ``.ppt`` file.
        max_images: At most this many pictures are returned.
        max_bytes: Pictures larger than this are skipped.

    Returns:
        ``(name, bytes)`` of the JPEG, PNG and TIFF pictures (vector art and DIBs are left out).
    """
    try:
        import olefile

        with olefile.OleFileIO(path) as ole:
            if not ole.exists("Pictures"):
                return []
            stream = ole.openstream("Pictures").read()
    except Exception:  # noqa: BLE001 - a damaged container simply has no pictures to offer
        return []
    pictures: List[Tuple[str, bytes]] = []
    for _off, instance, _version, rtype, body, body_end in _records(stream, 0, len(stream)):
        blip = _BLIP_TYPES.get(rtype)
        if blip is None:
            continue
        suffix, two_uid_instances = blip
        start = body + 16 + (16 if instance in two_uid_instances else 0) + 1
        data = stream[start:body_end]
        if 0 < len(data) <= max_bytes:
            pictures.append((f"picture{len(pictures) + 1}{suffix}", data))
        if len(pictures) >= max_images:
            break
    return pictures


# ---------------------------------------------------------------------------
# Truncated OOXML packages
# ---------------------------------------------------------------------------

class TruncatedPackageError(ValueError):
    """Nothing could be salvaged from the package (the message says why)."""


_LOCAL_FILE_HEADER = b"PK\x03\x04"
_TEXT_RUN_TAGS = {
    "{http://schemas.openxmlformats.org/drawingml/2006/main}t",
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t",
}
_PARAGRAPH_TAGS = {
    "{http://schemas.openxmlformats.org/drawingml/2006/main}p",
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p",
}
_SLIDE_MEMBER = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_BODY_MEMBERS = ("word/document.xml",)


def _complete_members(data: bytes) -> Tuple[dict, int]:
    """Members readable from the local file headers of a (possibly truncated) zip.

    Returns:
        ``(members, cut)``: member name -> bytes of every member whose data is complete and
        inflates, and the number of members that were cut off or damaged.
    """
    import zlib

    members: dict = {}
    cut = 0
    off = 0
    while off + 30 <= len(data) and data[off:off + 4] == _LOCAL_FILE_HEADER:
        _sig, _ver, flags, method, _t, _d, _crc, csize, _usize, name_len, extra_len = struct.unpack_from("<IHHHHHIIIHH", data, off)
        name = data[off + 30:off + 30 + name_len].decode("utf-8", errors="replace").replace(_BACKSLASH, "/")
        start = off + 30 + name_len + extra_len
        if flags & 0x08:
            break  # sizes only in a data descriptor after the data: not walkable without the directory
        payload = data[start:start + csize]
        if len(payload) < csize:
            cut += 1
            break
        try:
            members[name] = zlib.decompress(payload, -15) if method == 8 else payload
        except zlib.error:
            cut += 1
        off = start + csize
    return members, cut


def _xml_paragraphs(xml: bytes) -> List[str]:
    """Paragraph texts of a DrawingML / WordprocessingML part (text runs joined per paragraph)."""
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    paragraphs: List[str] = []
    for paragraph in root.iter():
        if paragraph.tag not in _PARAGRAPH_TAGS:
            continue
        text = "".join(run.text or "" for run in paragraph.iter() if run.tag in _TEXT_RUN_TAGS).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def truncated_ooxml_text(path: str) -> Tuple[str, str]:
    """Text of the slides or body of an OOXML package that was cut off (no central directory).

    A copy that stopped mid-way (the two decks of one corpus end inside a picture) is not a
    zip file to ``zipfile`` or Docling, yet every part before the cut is complete and
    inflates on its own. The local file headers are walked instead, and the text of the
    surviving ``ppt/slides/slideN.xml`` parts (in slide order) or of ``word/document.xml``
    is returned as paragraphs; pictures, table layout and notes are not recovered.

    Args:
        path: The ``.pptx`` / ``.docx`` file.

    Returns:
        ``(text, note)``: the recovered text with one ``## Slide N`` heading per slide, and a
        one-line description of what survived for the log.

    Raises:
        TruncatedPackageError: Not a zip at all, or no text part survived the cut.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != _LOCAL_FILE_HEADER:
        raise TruncatedPackageError("not a zip package")
    members, cut = _complete_members(data)
    if not members:
        raise TruncatedPackageError("no complete package member before the cut")
    sections: List[str] = []
    slides = sorted((int(m.group(1)), name) for name, m in ((n, _SLIDE_MEMBER.match(n)) for n in members) if m)
    for number, name in slides:
        paragraphs = _xml_paragraphs(members[name])
        if paragraphs:
            sections.append(f"## Slide {number}\n\n" + "\n\n".join(paragraphs))
    for name in _BODY_MEMBERS:
        if name in members:
            paragraphs = _xml_paragraphs(members[name])
            if paragraphs:
                sections.append("\n\n".join(paragraphs))
    if not sections:
        raise TruncatedPackageError(f"no text part among the {len(members)} complete members before the cut")
    note = f"{len(members)} complete members, {cut} cut off, {len(slides)} slide part(s)"
    return "\n\n".join(sections), note
