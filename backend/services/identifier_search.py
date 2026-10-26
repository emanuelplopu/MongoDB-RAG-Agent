"""Identifier-aware retrieval for questions that name an Austrian legal identifier.

Ordinary questions never touch this module beyond :func:`detect_identifiers`, which
returns an empty list for them. When a question names a Firmenbuchnummer
(``FN 404945h``), a Grundbuch entry (``EZ 52`` together with ``KG 11029``) or a
court file number (Geschäftszahl, ``13 U 39/16v``, ``17 Cg 114/11a``), the two
hybrid retrieval paths (``hybrid_search`` in ``backend/routers/search.py`` and
``FederatedSearch._search_database``) switch to the identifier path:

1. The normal vector and fuzzy text stages run unchanged, but fetch
   :data:`CANDIDATE_MULTIPLIER` × ``match_count`` candidates per index with
   ``numCandidates`` of at least :data:`NUM_CANDIDATES_FACTOR` × that limit, and
   project the fields the copy collapse needs (``chunk_index`` and the parent
   document's ``content_hash``).
2. One additional Atlas Search query on the text index matches the identifier's
   spelling variants as exact ``phrase`` clauses (no fuzziness), so ``404945h``
   cannot drift to another number and ``13 U 39/16v`` finds ``13 U 39/16 v``.
   Documents whose ``source`` path or ``title`` carries the identifier are
   matched too (a tolerant regex, then their chunks by ``document_id``): the
   number of a Firmenbuchauszug often survives only in its file name, because
   the extract's text lost it in conversion.
3. :func:`merge_identifier_results` pins those exact hits ahead of the fused list
   and collapses copies of the same file (same ``content_hash`` and
   ``chunk_index``) into one representative whose ``metadata.locations`` lists
   the other source paths. The remaining slots are filled from the fused list.

Background: findings F25 and F17 of
``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# How many exact hits the phrase query returns at most.
EXACT_HIT_LIMIT = 20
# Candidates fetched per index on the identifier path, as a multiple of match_count.
CANDIDATE_MULTIPLIER = 5
# numCandidates for $vectorSearch on the identifier path, as a multiple of its limit.
NUM_CANDIDATES_FACTOR = 20
# numCandidates floor; the ordinary path uses this fixed value.
DEFAULT_NUM_CANDIDATES = 100
# Score given to pinned exact hits; above every fused (RRF) score on both paths.
PINNED_SCORE = 1.0
# Documents whose source path or title carries the identifier: how many to take,
# and how many of each one's chunks (lowest chunk_index first) become exact hits.
TITLE_MATCH_DOCUMENT_LIMIT = 20
TITLE_MATCH_CHUNKS_PER_DOCUMENT = 8
# Document fields the identifier's regex is matched against.
TITLE_MATCH_FIELDS: Tuple[str, ...] = ("source", "title")

# Extra $project fields the collapse needs; merged into the vector/text pipelines
# on the identifier path only. The $lookup alias "doc_info" is shared by all pipelines.
COPY_FIELDS_PROJECTION: Dict[str, Any] = {
    "chunk_index": 1,
    "content_hash": "$doc_info.content_hash",
}

KIND_FIRMENBUCH = "firmenbuch"
KIND_GRUNDBUCH = "grundbuch"
KIND_GESCHAEFTSZAHL = "geschaeftszahl"

# FN 404945h / FN404945h / FN 404945 h — number plus check letter.
_FN_RE = re.compile(r"(?<![\w/])FN\s?(\d{4,7})\s?([A-Za-z])(?![\w/])", re.IGNORECASE)
# EZ 52 / EZ52 — Einlagezahl; only an identifier together with a KG number.
_EZ_RE = re.compile(r"(?<![\w/])EZ\s?(\d{1,6})(?![\w/])", re.IGNORECASE)
# KG 11029 / KG11029 — Katastralgemeinde number (five digits).
_KG_RE = re.compile(r"(?<![\w/])KG\s?(\d{5})(?![\w/])", re.IGNORECASE)
# 13 U 39/16v, 13 U 39/16 v, 17 Cg 114/11a, 038 C 1199/2017a, 5 E 1149/20k-12
# department, Gattungszeichen, running number / year, check letter, optional ON suffix.
_GZ_RE = re.compile(
    r"(?<![\w/])(\d{1,3})\s+([A-Za-z]{1,4})\s+(\d{1,5})/(\d{2}(?:\d{2})?)\s?([A-Za-z])(?:-(\d{1,3}))?(?![\w/])"
)


@dataclass(frozen=True)
class LegalIdentifier:
    """One identifier found in a query, with the spellings the exact query matches.

    Attributes:
        kind: One of ``firmenbuch``, ``grundbuch`` or ``geschaeftszahl``.
        canonical: Normalised display form (``FN 404945h``, ``EZ 52 KG 11029``,
            ``13 U 39/16v``).
        groups: Spelling variants per part. A chunk must contain one variant of
            every group: one group for FN and Geschäftszahl, two (EZ, KG) for a
            Grundbuch entry.
        patterns: Case-insensitive regexes (PCRE and Python compatible) that a
            document's ``source`` or ``title`` must all match; tolerant about
            spaces and about the slash of a Geschäftszahl, which file names
            replace (``17Cg114_11a``).
        position: Character offset of the identifier in the query (for ordering).
    """

    kind: str
    canonical: str
    groups: Tuple[Tuple[str, ...], ...]
    patterns: Tuple[str, ...] = ()
    position: int = 0

    @property
    def variants(self) -> Tuple[str, ...]:
        """All spelling variants across the groups, in order."""
        return tuple(variant for group in self.groups for variant in group)


def _unique(values: Iterable[str]) -> Tuple[str, ...]:
    """Keep the first occurrence of every value, preserving order."""
    seen: List[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _case_variants(letter: str) -> Tuple[str, str]:
    """Lower and upper spelling of a check letter."""
    return letter.lower(), letter.upper()


def _firmenbuch_variants(number: str, letter: str) -> Tuple[str, ...]:
    """Spellings of a Firmenbuchnummer: with/without spaces, both letter cases, bare number."""
    variants: List[str] = []
    for check in _case_variants(letter):
        variants.extend([
            f"FN {number}{check}",
            f"FN{number}{check}",
            f"FN {number} {check}",
            f"{number}{check}",
        ])
    return _unique(variants)


def _geschaeftszahl_variants(
    department: str, kind: str, number: str, year: str, letter: str, suffix: Optional[str]
) -> Tuple[str, ...]:
    """Spellings of a Geschäftszahl: check letter with/without space and in both cases.

    A leading-zero department (``038 C``) is also emitted without the zeros, and
    an ON suffix (``-12``) is emitted both with and without the suffix.
    """
    departments = _unique([department, department.lstrip("0") or department])
    variants: List[str] = []
    for dept in departments:
        base = f"{dept} {kind} {number}/{year}"
        for check in _case_variants(letter):
            for spelled in (f"{base}{check}", f"{base} {check}"):
                variants.append(spelled)
                if suffix:
                    variants.append(f"{spelled}-{suffix}")
    return _unique(variants)


def _grundbuch_groups(ez: str, kg: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Spellings of the EZ and KG parts of a Grundbuch entry."""
    return (f"EZ {ez}", f"EZ{ez}"), (f"KG {kg}", f"KG{kg}")


def _firmenbuch_pattern(number: str, letter: str) -> str:
    """Regex for a Firmenbuchnummer in a file name or title (``FN 404945h``, ``FN404945h``, ``FN 404945 h``)."""
    return rf"(?<![A-Za-z])FN\s*{re.escape(number)}\s*{re.escape(letter)}(?![A-Za-z0-9])"


def _geschaeftszahl_pattern(department: str, kind: str, number: str, year: str, letter: str) -> str:
    """Regex for a Geschäftszahl in a file name or title; the slash may be ``_``, ``-``, ``.`` or a space."""
    department_digits = re.escape(department.lstrip("0") or department)
    return (
        rf"(?<!\d)0*{department_digits}\s*{re.escape(kind)}\s*{re.escape(number)}"
        rf"\s*[/_.\- ]?\s*{re.escape(year)}\s*{re.escape(letter)}(?![A-Za-z0-9])"
    )


def _grundbuch_patterns(ez: str, kg: str) -> Tuple[str, str]:
    """Regexes for the EZ and the KG part; a document must match both."""
    return (
        rf"(?<![A-Za-z])EZ\s*{re.escape(ez)}(?![0-9])",
        rf"(?<![A-Za-z])KG\s*{re.escape(kg)}(?![0-9])",
    )


def detect_identifiers(text: str) -> List[LegalIdentifier]:
    """Find Austrian legal identifiers in a query.

    Args:
        text: The user's question or search query.

    Returns:
        The identifiers in order of appearance, one entry per distinct canonical
        form. Empty for ordinary queries, which is the signal that the identifier
        path must not run.
    """
    if not text:
        return []
    found: List[LegalIdentifier] = []

    for match in _FN_RE.finditer(text):
        number, letter = match.group(1), match.group(2)
        found.append(LegalIdentifier(
            kind=KIND_FIRMENBUCH,
            canonical=f"FN {number}{letter.lower()}",
            groups=(_firmenbuch_variants(number, letter),),
            patterns=(_firmenbuch_pattern(number, letter),),
            position=match.start(),
        ))

    for match in _GZ_RE.finditer(text):
        department, kind, number, year, letter, suffix = match.groups()
        canonical = f"{department} {kind} {number}/{year}{letter.lower()}"
        if suffix:
            canonical = f"{canonical}-{suffix}"
        found.append(LegalIdentifier(
            kind=KIND_GESCHAEFTSZAHL,
            canonical=canonical,
            groups=(_geschaeftszahl_variants(department, kind, number, year, letter, suffix),),
            patterns=(_geschaeftszahl_pattern(department, kind, number, year, letter),),
            position=match.start(),
        ))

    ez_matches = list(_EZ_RE.finditer(text))
    kg_matches = list(_KG_RE.finditer(text))
    if ez_matches and kg_matches:
        for ez_match in ez_matches:
            # Pair every EZ with the nearest KG number in the text.
            kg_match = min(kg_matches, key=lambda m: abs(m.start() - ez_match.start()))
            ez, kg = ez_match.group(1), kg_match.group(1)
            found.append(LegalIdentifier(
                kind=KIND_GRUNDBUCH,
                canonical=f"EZ {ez} KG {kg}",
                groups=_grundbuch_groups(ez, kg),
                patterns=_grundbuch_patterns(ez, kg),
                position=min(ez_match.start(), kg_match.start()),
            ))

    unique: List[LegalIdentifier] = []
    seen: set[str] = set()
    for identifier in sorted(found, key=lambda item: item.position):
        if identifier.canonical not in seen:
            seen.add(identifier.canonical)
            unique.append(identifier)
    return unique


def identifier_fetch_count(match_count: int) -> int:
    """Candidates to fetch per index on the identifier path."""
    return max(1, match_count) * CANDIDATE_MULTIPLIER


def vector_num_candidates(limit: int) -> int:
    """``numCandidates`` for a ``$vectorSearch`` with the given limit on the identifier path."""
    return max(DEFAULT_NUM_CANDIDATES, NUM_CANDIDATES_FACTOR * max(1, limit))


def _phrase_clauses(variants: Sequence[str], path: str) -> List[Dict[str, Any]]:
    """One exact ``phrase`` clause per spelling variant."""
    return [{"phrase": {"query": variant, "path": path}} for variant in variants]


def build_exact_search_operator(
    identifiers: Sequence[LegalIdentifier], *, index: str, path: str = "content"
) -> Dict[str, Any]:
    """The ``$search`` operator body that matches any of the identifiers exactly.

    Single-part identifiers contribute one ``phrase`` clause per variant to the
    top-level ``compound.should``. A Grundbuch entry contributes one nested
    ``compound`` that requires an EZ variant and a KG variant in the same chunk,
    because an Einlagezahl alone repeats in every Katastralgemeinde.
    """
    should: List[Dict[str, Any]] = []
    for identifier in identifiers:
        if len(identifier.groups) == 1:
            should.extend(_phrase_clauses(identifier.groups[0], path))
        else:
            should.append({
                "compound": {
                    "must": [
                        {"compound": {"should": _phrase_clauses(group, path), "minimumShouldMatch": 1}}
                        for group in identifier.groups
                    ]
                }
            })
    return {"index": index, "compound": {"should": should, "minimumShouldMatch": 1}}


def build_exact_pipeline(
    identifiers: Sequence[LegalIdentifier],
    *,
    text_index: str,
    docs_collection: str,
    limit: int = EXACT_HIT_LIMIT,
) -> List[Dict[str, Any]]:
    """Aggregation pipeline for the exact identifier query on the chunks collection.

    The projection matches the vector/text pipelines of both callers and adds the
    copy fields (``chunk_index``, ``content_hash``) the collapse needs.
    """
    return [
        {"$search": build_exact_search_operator(identifiers, index=text_index)},
        {"$limit": limit},
        {
            "$lookup": {
                "from": docs_collection,
                "localField": "document_id",
                "foreignField": "_id",
                "as": "doc_info",
            }
        },
        {"$unwind": {"path": "$doc_info", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "chunk_id": "$_id",
                "document_id": 1,
                "content": 1,
                "similarity": {"$meta": "searchScore"},
                "metadata": 1,
                "document_title": {"$ifNull": ["$doc_info.title", "Unknown"]},
                "document_source": {"$ifNull": ["$doc_info.source", ""]},
                **COPY_FIELDS_PROJECTION,
            }
        },
    ]


def build_source_filter(
    identifiers: Sequence[LegalIdentifier], fields: Sequence[str] = TITLE_MATCH_FIELDS
) -> Dict[str, Any]:
    """MongoDB filter for documents whose ``source`` or ``title`` carries one of the identifiers.

    Every pattern of an identifier must match (both parts of a Grundbuch entry);
    any identifier qualifies the document.
    """
    def pattern_filter(pattern: str) -> Dict[str, Any]:
        return {"$or": [{field: {"$regex": pattern, "$options": "i"}} for field in fields]}

    clauses: List[Dict[str, Any]] = []
    for identifier in identifiers:
        if not identifier.patterns:
            continue
        if len(identifier.patterns) == 1:
            clauses.append(pattern_filter(identifier.patterns[0]))
        else:
            clauses.append({"$and": [pattern_filter(pattern) for pattern in identifier.patterns]})
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


async def fetch_title_hits(
    documents: Any,
    chunks: Any,
    identifiers: Sequence[LegalIdentifier],
    *,
    document_limit: int = TITLE_MATCH_DOCUMENT_LIMIT,
    chunks_per_document: int = TITLE_MATCH_CHUNKS_PER_DOCUMENT,
) -> List[Dict[str, Any]]:
    """Chunks of the documents whose source path or title carries an identifier.

    The regex runs over the documents collection (one row per file, cheap) and
    the chunks come by the indexed ``document_id``; the rows have the shape of
    the search pipelines' projection plus ``chunk_index``/``content_hash``.
    Documents come in find order, their chunks by ``chunk_index``; the merge
    decides which of them lead.
    """
    if not identifiers:
        return []
    source_filter = build_source_filter(identifiers)
    matched: Dict[Any, Dict[str, Any]] = {}
    cursor = documents.find(source_filter, {"title": 1, "source": 1, "content_hash": 1}).limit(document_limit)
    async for doc in cursor:
        matched[doc["_id"]] = doc
    if not matched:
        return []

    taken: Dict[Any, int] = {doc_id: 0 for doc_id in matched}
    hits: List[Dict[str, Any]] = []
    chunk_cursor = chunks.find(
        {"document_id": {"$in": list(matched)}},
        {"content": 1, "chunk_index": 1, "metadata": 1, "document_id": 1},
    ).sort("chunk_index", 1)
    async for chunk in chunk_cursor:
        doc_id = chunk.get("document_id")
        if doc_id not in taken or taken[doc_id] >= chunks_per_document:
            continue
        taken[doc_id] += 1
        parent = matched[doc_id]
        hits.append({
            "chunk_id": chunk.get("_id"),
            "document_id": doc_id,
            "content": chunk.get("content", ""),
            "similarity": 0.0,
            "metadata": chunk.get("metadata") or {},
            "document_title": parent.get("title") or "Unknown",
            "document_source": parent.get("source") or "",
            "chunk_index": chunk.get("chunk_index"),
            "content_hash": parent.get("content_hash"),
            "search_type": "exact",
        })
    # Keep document order (find order), chunks by index within a document.
    order = {doc_id: position for position, doc_id in enumerate(matched)}
    hits.sort(key=lambda hit: (order[hit["document_id"]], hit["chunk_index"] if hit["chunk_index"] is not None else -1))
    return hits


async def fetch_exact_hits(
    collection: Any,
    identifiers: Sequence[LegalIdentifier],
    *,
    text_index: str,
    docs_collection: str,
    documents: Any = None,
    limit: int = EXACT_HIT_LIMIT,
) -> List[Dict[str, Any]]:
    """Run the exact identifier queries and tag the hits with ``search_type="exact"``.

    Phrase hits on the chunk text come first (search order), then the chunks of
    documents whose source path or title carries the identifier (when a
    ``documents`` collection is given); a chunk found by both is listed once.
    A failing query must not break the request: it is logged and the caller
    continues with what the other query, or the fused list alone, returned.

    Args:
        collection: The chunks collection.
        identifiers: Identifiers found in the query.
        text_index: Name of the Atlas Search text index on ``content``.
        docs_collection: Name of the documents collection (for ``$lookup``).
        documents: The documents collection object, for the source/title match.
        limit: Maximum phrase hits.
    """
    if not identifiers:
        return []
    names = [identifier.canonical for identifier in identifiers]
    pipeline = build_exact_pipeline(
        identifiers, text_index=text_index, docs_collection=docs_collection, limit=limit
    )
    hits: List[Dict[str, Any]] = []
    try:
        cursor = collection.aggregate(pipeline)
        async for doc in cursor:
            doc["search_type"] = "exact"
            hits.append(doc)
    except Exception as exc:  # noqa: BLE001 - the fused list is still a valid answer
        logger.warning("Exact identifier phrase search failed for %s: %s", names, exc)
    phrase_count = len(hits)

    title_hits: List[Dict[str, Any]] = []
    if documents is not None:
        try:
            title_hits = await fetch_title_hits(documents, collection, identifiers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Identifier source/title match failed for %s: %s", names, exc)

    seen = {str(hit.get("chunk_id")) for hit in hits}
    for hit in title_hits:
        if str(hit.get("chunk_id")) not in seen:
            seen.add(str(hit.get("chunk_id")))
            hits.append(hit)
    logger.debug(
        "Exact identifier search for %s: %d phrase hit(s), %d title hit(s), %d combined",
        names, phrase_count, len(title_hits), len(hits),
    )
    return hits


def copy_group_key(
    content_hash: Optional[str], chunk_index: Optional[int], chunk_id: Any
) -> Tuple[Any, ...]:
    """Key under which copies of the same chunk collapse.

    Copies of one file share the parent document's ``content_hash`` and, because
    chunking is deterministic, the same ``chunk_index``. Without both values the
    chunk id is the key, so nothing collapses by accident.
    """
    if content_hash and chunk_index is not None:
        return ("hash", content_hash, int(chunk_index))
    return ("id", str(chunk_id))


def _result_group_key(doc: Dict[str, Any]) -> Tuple[Any, ...]:
    metadata = doc.get("metadata") or {}
    content_hash = doc.get("content_hash") or metadata.get("content_hash")
    chunk_index = doc.get("chunk_index")
    if chunk_index is None:
        chunk_index = metadata.get("chunk_index")
    return copy_group_key(content_hash, chunk_index, doc.get("chunk_id"))


def add_location(locations: List[str], own_source: str, other_source: str, other_locations: Iterable[str] = ()) -> None:
    """Record another copy's source path (and the ones it already collected) on a representative."""
    for source in (other_source, *other_locations):
        if source and source != own_source and source not in locations:
            locations.append(source)


def merge_identifier_results(
    exact_hits: Sequence[Dict[str, Any]],
    fused: Sequence[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Pin exact hits ahead of the fused list and collapse copies.

    Order: exact hits that the normal search also found come first (they match
    the rest of the question too), then the other exact hits in search order,
    then the fused list in its own order. Copies, identified by
    :func:`copy_group_key`, keep their first representative; every other copy's
    source path is appended to ``metadata.locations``. Pinned representatives
    get ``metadata.exact_identifier_match = True`` and :data:`PINNED_SCORE` as
    their ``similarity``/``rrf_score``.

    Args:
        exact_hits: Results of the exact identifier query (search order).
        fused: The RRF-fused vector/text list (best first), not yet truncated.
        limit: Number of results to return.

    Returns:
        At most ``limit`` result dicts; the metadata dicts are copies.
    """
    fused_ids = {str(doc.get("chunk_id")) for doc in fused}
    pinned = sorted(
        enumerate(exact_hits),
        key=lambda item: (0 if str(item[1].get("chunk_id")) in fused_ids else 1, item[0]),
    )

    representatives: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []
    collapsed = 0
    candidates = [(doc, True) for _, doc in pinned] + [(doc, False) for doc in fused]
    for doc, is_pinned in candidates:
        key = _result_group_key(doc)
        representative = representatives.get(key)
        if representative is None:
            representative = dict(doc)
            metadata = dict(doc.get("metadata") or {})
            metadata["locations"] = list(metadata.get("locations") or [])
            if is_pinned:
                metadata["exact_identifier_match"] = True
                representative["similarity"] = PINNED_SCORE
                representative["rrf_score"] = PINNED_SCORE
            representative["metadata"] = metadata
            representatives[key] = representative
            ordered.append(representative)
            continue
        collapsed += 1
        add_location(
            representative["metadata"]["locations"],
            representative.get("document_source") or "",
            doc.get("document_source") or "",
            (doc.get("metadata") or {}).get("locations") or [],
        )

    logger.debug(
        "Identifier merge: %d exact hit(s), %d fused, %d cop(y/ies) collapsed, %d returned",
        len(exact_hits), len(fused), collapsed, min(len(ordered), limit),
    )
    return ordered[:limit]
