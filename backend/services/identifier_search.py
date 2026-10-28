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

Statute citations (``§ 1 TKG 2021``, ``§ 365 ZPO``, ``§ 3 Abs 1 MRG``,
``Art 6 EMRK``, ``§§ 332, 365 ZPO``) and bare statute names with a year
(``TKG 2021``, ``Telekommunikationsgesetz 2021``) are identifiers of kind
:data:`KIND_STATUTE`. They take the identifier path above and change three more
things, for these queries only (finding F34):

a. The text stage of the hybrid search is not the fuzzy bag of words but a
   ``compound`` of exact ``phrase`` clauses on the citation (section number plus
   statute abbreviation) and on the statute name, see :func:`plan_text_stage`.
b. The rest of the question still runs as a fuzzy ``text`` clause, but without
   its pure-number tokens (``1``, ``2021``, ``18.08.2022``), which the fuzzy
   stage matched in unrelated tables and numbered lists.
c. The vector stage's top :data:`SEMANTIC_GUARANTEE_COUNT` results get a
   guaranteed place in the fused result window, directly behind the pinned
   exact hits (``metadata.semantic_guarantee``).

A statute's own text (a RIS print) rarely names the statute next to each
section, so for citations the chunks of documents whose title or source names
the statute are matched against the section marker (``§ 1.`` / ``§ 1 (1)``,
RIS spellings) instead of being taken by position.

Background: findings F25, F17 and F34 of
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

# Vector results that keep a place in the fused window on a statute query (F34).
SEMANTIC_GUARANTEE_COUNT = 3
# The fuzzy options of the ordinary text stage; the statute path reuses them for the remainder.
TEXT_FUZZY: Dict[str, int] = {"maxEdits": 2, "prefixLength": 3}

KIND_FIRMENBUCH = "firmenbuch"
KIND_GRUNDBUCH = "grundbuch"
KIND_GESCHAEFTSZAHL = "geschaeftszahl"
KIND_STATUTE = "statute"

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

# Statute citations. Letters of a German statute name or abbreviation.
_LETTERS = "A-Za-zÄÖÜäöüß"
# A statute abbreviation: starts with a capital and has at least two capitals
# (TKG, ZPO, GewO, UrhG, ÄrzteG, B-VG, EMRK, GmbHG); never Abs, Art, Z, lit, Satz.
_ABBREVIATION = (
    rf"(?=(?:[{_LETTERS}]|-)*[A-ZÄÖÜ](?:[{_LETTERS}]|-)*[A-ZÄÖÜ])"
    rf"[A-ZÄÖÜ][{_LETTERS}]*(?:-[{_LETTERS}]+)*"
)
# Subdivisions between the section number and the statute: Abs 1, Abs. 2, Z 3, lit a, Satz 2, Nr 4.
_SUBDIVISION = (
    r"(?:Abs\.?|Absatz|Z\.?|Ziffer|lit\.?|litera|Satz|Nr\.?|Halbsatz)\s*(?:\d{1,3}|[a-z])"
    r"(?:\s*(?:,|und|bis|oder|-|–)\s*(?:\d{1,3}|[a-z]))*"
)
# § 1 TKG 2021, § 365 ZPO, § 3 Abs 1 MRG, §§ 332, 365 ZPO, Art 6 EMRK, Art. 6 Abs 1 EMRK, § 1295 des ABGB
_STATUTE_CITATION_RE = re.compile(
    rf"(?<![{_LETTERS}0-9])"
    r"(§§?|Artikel|Art\.?|Paragraph(?:en)?)\s*"
    r"(\d{1,4}[a-z]{0,2}(?:\s*(?:,|und|bis|-|–)\s*\d{1,4}[a-z]{0,2})*)"
    rf"((?:\s+f{{1,2}}\.?(?=\s))?(?:\s*{_SUBDIVISION})*)"
    r"(?:\s+(?:des|der|dem))?\s+"
    rf"({_ABBREVIATION})"
    r"(?:\s+((?:1[89]|20)\d{2}))?"
    rf"(?![{_LETTERS}0-9])"
)
# Bare statute names with a year: TKG 2021, GewO 1994, Telekommunikationsgesetz 2021.
# An abbreviation needs three letters and the usual ending (…G, …B, …O, …K, …V):
# "GJ 2021" (Geschäftsjahr), "AG 2021" and "OGH 2021" are not statutes.
_STATUTE_NAME_RE = re.compile(
    rf"(?<![{_LETTERS}0-9§/])"
    rf"((?:(?=[{_LETTERS}-]{{3,}}(?![{_LETTERS}])){_ABBREVIATION}(?<=[GBOKV]))"
    rf"|(?:[A-ZÄÖÜ][{_LETTERS}]*(?:-[{_LETTERS}]+)*(?:gesetz|ordnung|verordnung|novelle|buch)))"
    r"\s+((?:1[89]|20)\d{2})"
    rf"(?![{_LETTERS}0-9/])"
)
# RIS prints mark a section heading as "§ 1." or "§ 1 (1)"; a citation in running text has neither.
_HEADING_TAIL_RE = re.compile(r"\.(?!\d)|[^0-9A-Za-z§(]{0,3}\(\d")
# A token that carries no letter is a pure number (or punctuation) and leaves the fuzzy clause.
_HAS_LETTER_RE = re.compile(rf"[{_LETTERS}]")


@dataclass(frozen=True)
class LegalIdentifier:
    """One identifier found in a query, with the spellings the exact query matches.

    Attributes:
        kind: One of ``firmenbuch``, ``grundbuch``, ``geschaeftszahl`` or ``statute``.
        canonical: Normalised display form (``FN 404945h``, ``EZ 52 KG 11029``,
            ``13 U 39/16v``, ``§ 1 TKG 2021``).
        groups: Spelling variants per part. A chunk must contain one variant of
            every group: one group for FN, Geschäftszahl and statute citations
            (``§ 1 TKG 2021``, ``§ 1 TKG``), two (EZ, KG) for a Grundbuch entry.
            Empty for a bare statute name, which pins nothing.
        patterns: Case-insensitive regexes (PCRE and Python compatible) that a
            document's ``source`` or ``title`` must all match; tolerant about
            spaces and about the slash of a Geschäftszahl, which file names
            replace (``17Cg114_11a``). For a statute citation: the statute's
            name, with its year when the query gives one.
        position: Character offset of the identifier in the query (for ordering).
        length: Length of the matched text in the query; the statute path blanks
            the citation out of the fuzzy remainder with it.
        name_variants: Statute only: the statute name as ``phrase`` clauses of
            the text stage (``TKG 2021``, ``TKG``).
        section_patterns: Statute citation only: regexes a chunk of a document
            named after the statute must match to count as the cited section
            (``§ 1``, tolerant about the separator, not ``§ 10`` or ``§ 1a``).
    """

    kind: str
    canonical: str
    groups: Tuple[Tuple[str, ...], ...]
    patterns: Tuple[str, ...] = ()
    position: int = 0
    length: int = 0
    name_variants: Tuple[str, ...] = ()
    section_patterns: Tuple[str, ...] = ()

    @property
    def variants(self) -> Tuple[str, ...]:
        """All spelling variants across the groups, in order."""
        return tuple(variant for group in self.groups for variant in group)

    @property
    def end(self) -> int:
        """Character offset just past the identifier in the query."""
        return self.position + self.length


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


def _statute_prefix(prefix: str, sections: Sequence[str]) -> str:
    """Canonical prefix of a citation: ``§``/``§§`` by section count, ``Art`` for articles."""
    if prefix.startswith("§"):
        return "§§" if len(sections) > 1 else "§"
    if prefix.startswith("Art"):
        return "Art"
    return "§§" if len(sections) > 1 else "§"


def _statute_subdivision(raw: str) -> str:
    """Normalised subdivisions (``Abs. 1`` -> ``Abs 1``); empty when there are none."""
    return " ".join(raw.replace(".", "").split())


def _statute_name_variants(name: str, year: Optional[str]) -> Tuple[str, ...]:
    """The statute name as phrase spellings: with its year first, then bare."""
    return _unique([f"{name} {year}", name] if year else [name])


def _statute_citation_variants(
    prefix: str, sections: Sequence[str], subdivision: str, name: str, year: Optional[str], raw: str
) -> Tuple[str, ...]:
    """Spellings of a citation that a chunk must contain to be an exact hit.

    Section number plus statute name, adjacent: ``§ 1 TKG 2021`` and ``§ 1 TKG``;
    with a subdivision also ``§ 3 Abs 1 MRG`` / ``§ 3 Abs. 1 MRG``; articles as
    ``Art``, ``Art.`` and ``Artikel``. Every variant names the statute, because
    the standard analyzer drops ``§`` and a phrase ``§ 1`` alone would be the
    bare token ``1`` that matches every numbered list (F34). The RIS heading
    spellings ``§ 1.`` / ``§ 1 (1)`` are therefore matched in the statute's own
    text through :attr:`LegalIdentifier.section_patterns`, not as phrases.
    """
    heads: List[str]
    if prefix == "Art":
        heads = ["Art", "Art.", "Artikel"]
    else:
        heads = ["§"]
    variants: List[str] = []
    for section in sections:
        for head in heads:
            core = f"{head} {section}"
            if year:
                variants.append(f"{core} {name} {year}")
            variants.append(f"{core} {name}")
            if subdivision:
                variants.append(f"{core} {subdivision} {name}")
                dotted = re.sub(r"\b(Abs|Z|lit|Nr)\b(?!\.)", r"\1.", subdivision)
                if dotted != subdivision:
                    variants.append(f"{core} {dotted} {name}")
    if len(sections) > 1:
        # The citation as written, e.g. "§§ 332, 365 ZPO".
        variants.append(" ".join(raw.replace(",", ", ").split()))
    return _unique(variants)


def _statute_title_pattern(name: str, year: Optional[str]) -> str:
    """Regex for the statute name in a file name or title.

    ``TKG 2021`` must also match ``TKG\\xa02021`` (a no-break space, as in the
    live RIS print's file name) and ``TKG_2021``; without a year the bare name
    is enough. Case-insensitivity comes from :func:`build_source_filter`.
    """
    pattern = rf"(?<![A-Za-z]){re.escape(name)}"
    if year:
        pattern += rf"[^0-9A-Za-z]{{0,3}}{re.escape(year)}(?![0-9])"
    else:
        pattern += r"(?![A-Za-z])"
    return pattern


def _statute_section_pattern(prefix: str, section: str) -> str:
    """Regex for the section marker in the statute's own text (``§ 1.``, ``§1 (1)``, ``§§ 1 bis``)."""
    number = re.escape(section)
    if prefix == "Art":
        return rf"Art(?:ikel|\.)?[^0-9A-Za-z]{{0,3}}{number}(?![0-9a-z])"
    return rf"§{{1,2}}[^0-9A-Za-z§]{{0,3}}{number}(?![0-9a-z])"


def _statute_citation(match: "re.Match[str]") -> LegalIdentifier:
    """Build the identifier of one statute citation match."""
    raw_prefix, raw_sections, raw_subdivision, name, year = match.groups()
    sections = re.findall(r"\d{1,4}[a-z]{0,2}", raw_sections)
    prefix = _statute_prefix(raw_prefix, sections)
    subdivision = _statute_subdivision(raw_subdivision)
    section_text = " ".join(raw_sections.replace(",", ", ").split())
    parts = [prefix, section_text]
    if subdivision:
        parts.append(subdivision)
    parts.append(name)
    if year:
        parts.append(year)
    return LegalIdentifier(
        kind=KIND_STATUTE,
        canonical=" ".join(parts),
        groups=(_statute_citation_variants(prefix, sections, subdivision, name, year, match.group(0)),),
        patterns=(_statute_title_pattern(name, year),),
        position=match.start(),
        length=match.end() - match.start(),
        name_variants=_statute_name_variants(name, year),
        section_patterns=tuple(_statute_section_pattern(prefix, section) for section in sections),
    )


def _statute_name(match: "re.Match[str]") -> LegalIdentifier:
    """Build the identifier of a bare statute name with a year (``TKG 2021``).

    It changes the text stage and guarantees the semantic top results, but pins
    nothing: there is no section to match, and a chunk that merely names the
    statute is not an exact hit.
    """
    name, year = match.groups()
    return LegalIdentifier(
        kind=KIND_STATUTE,
        canonical=f"{name} {year}",
        groups=(),
        patterns=(),
        position=match.start(),
        length=match.end() - match.start(),
        name_variants=_statute_name_variants(name, year),
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
            length=match.end() - match.start(),
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
            length=match.end() - match.start(),
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
                length=max(ez_match.end(), kg_match.end()) - min(ez_match.start(), kg_match.start()),
            ))

    citation_spans: List[Tuple[int, int]] = []
    for match in _STATUTE_CITATION_RE.finditer(text):
        found.append(_statute_citation(match))
        citation_spans.append((match.start(), match.end()))
    for match in _STATUTE_NAME_RE.finditer(text):
        # "TKG 2021" inside "§ 1 TKG 2021" belongs to the citation.
        if any(start < match.end() and match.start() < end for start, end in citation_spans):
            continue
        found.append(_statute_name(match))

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
        if not identifier.groups:
            continue  # a bare statute name pins nothing
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


def _is_section_heading(content: str, section_patterns: Sequence[str]) -> bool:
    """Whether a chunk carries the cited section as a heading (``§ 1.`` / ``§ 1 (1)``).

    A RIS print writes the section marker that way; ``gemäß § 1 des …`` in
    running text is a citation of another provision, not the section itself.
    """
    for pattern in section_patterns:
        for match in re.finditer(pattern, content):
            if _HEADING_TAIL_RE.match(content, match.end()):
                return True
    return False


async def _title_hits_for(
    documents: Any,
    chunks: Any,
    identifiers: Sequence[LegalIdentifier],
    section_patterns: Sequence[str],
    document_limit: int,
    chunks_per_document: int,
) -> List[Dict[str, Any]]:
    """Title hits for identifiers that share one chunk rule (see :func:`fetch_title_hits`)."""
    source_filter = build_source_filter(identifiers)
    matched: Dict[Any, Dict[str, Any]] = {}
    cursor = documents.find(source_filter, {"title": 1, "source": 1, "content_hash": 1}).limit(document_limit)
    async for doc in cursor:
        matched[doc["_id"]] = doc
    if not matched:
        return []

    chunk_filter: Dict[str, Any] = {"document_id": {"$in": list(matched)}}
    if section_patterns:
        chunk_filter["content"] = {"$regex": "|".join(f"(?:{pattern})" for pattern in section_patterns)}
    per_document: Dict[Any, List[Dict[str, Any]]] = {doc_id: [] for doc_id in matched}
    chunk_cursor = chunks.find(
        chunk_filter, {"content": 1, "chunk_index": 1, "metadata": 1, "document_id": 1},
    ).sort("chunk_index", 1)
    async for chunk in chunk_cursor:
        doc_id = chunk.get("document_id")
        if doc_id not in per_document:
            continue
        if not section_patterns and len(per_document[doc_id]) >= chunks_per_document:
            continue
        per_document[doc_id].append(chunk)

    hits: List[Dict[str, Any]] = []
    for doc_id, candidates in per_document.items():
        if section_patterns:
            # The section's own heading beats chunks that merely cite the section.
            headings = [c for c in candidates if _is_section_heading(c.get("content", ""), section_patterns)]
            candidates = (headings or candidates)[:chunks_per_document]
        parent = matched[doc_id]
        for chunk in candidates:
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

    For FN, Grundbuch and Geschäftszahl the first ``chunks_per_document`` chunks
    of each document are taken. For a statute citation only the chunks that
    carry the cited section (``section_patterns``, a regex on ``content`` over
    the matched documents' chunks) qualify, and among them the section heading
    (``§ 1.`` / ``§ 1 (1)``) beats mere citations of the section: the statute's
    § 1 is chunk 16 of a 500-chunk RIS print, not among its first eight. Bare
    statute names have no patterns and match no document.
    """
    if not identifiers:
        return []
    positional = [identifier for identifier in identifiers if identifier.patterns and not identifier.section_patterns]
    sectioned = [identifier for identifier in identifiers if identifier.patterns and identifier.section_patterns]
    hits: List[Dict[str, Any]] = []
    if positional:
        hits.extend(await _title_hits_for(documents, chunks, positional, (), document_limit, chunks_per_document))
    for identifier in sectioned:
        hits.extend(await _title_hits_for(
            documents, chunks, [identifier], identifier.section_patterns, document_limit, chunks_per_document,
        ))
    unique: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        if str(hit.get("chunk_id")) not in seen:
            seen.add(str(hit.get("chunk_id")))
            unique.append(hit)
    return unique


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
    hits: List[Dict[str, Any]] = []
    if any(identifier.groups for identifier in identifiers):
        pipeline = build_exact_pipeline(
            identifiers, text_index=text_index, docs_collection=docs_collection, limit=limit
        )
        try:
            cursor = collection.aggregate(pipeline)
            async for doc in cursor:
                doc["search_type"] = "exact"
                hits.append(doc)
        except Exception as exc:  # noqa: BLE001 - the fused list is still a valid answer
            logger.warning("Exact identifier phrase search failed for %s: %s", names, exc)
    phrase_count = len(hits)

    title_hits: List[Dict[str, Any]] = []
    if documents is not None and any(identifier.patterns for identifier in identifiers):
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


def has_statute_citation(identifiers: Sequence[LegalIdentifier]) -> bool:
    """Whether the query names a statute (citation or bare name): the F34 path applies."""
    return any(identifier.kind == KIND_STATUTE for identifier in identifiers)


def fuzzy_remainder(query: str, identifiers: Sequence[LegalIdentifier]) -> str:
    """The query without its statute citations and without pure-number tokens.

    ``Was regelt § 1 TKG 2021 (Zweck und Ziele) laut dem Gesetzestext in der
    Fassung vom 18.08.2022?`` becomes ``Was regelt (Zweck und Ziele) laut dem
    Gesetzestext in der Fassung vom``: the citation is matched as phrases, and
    ``1``, ``2021`` and ``18.08.2022`` are the tokens the fuzzy stage found in
    unrelated tables and numbered lists. Other identifier kinds stay in place.
    """
    chars = list(query)
    for identifier in identifiers:
        if identifier.kind == KIND_STATUTE and identifier.length:
            for offset in range(identifier.position, min(identifier.end, len(chars))):
                chars[offset] = " "
    tokens = [token for token in "".join(chars).split() if _HAS_LETTER_RE.search(token)]
    return " ".join(tokens)


@dataclass(frozen=True)
class TextStagePlan:
    """The ``$search`` operator of the text stage and how it was built.

    Attributes:
        operator: The ``$search`` stage body (``index`` plus ``text`` or ``compound``).
        mode: ``fuzzy`` (the ordinary stage, byte-identical to before) or
            ``statute`` (phrase/compound, F34 path).
        phrases: Phrase clauses of a ``statute`` plan, in operator order.
        remainder: The fuzzy remainder of a ``statute`` plan (empty when the
            query was nothing but the citation and no ``text`` clause was added).
    """

    operator: Dict[str, Any]
    mode: str
    phrases: Tuple[str, ...] = ()
    remainder: str = ""

    def describe(self) -> str:
        """One line for the log: which text stage ran and with what."""
        if self.mode != "statute":
            return "fuzzy text stage"
        return f"statute phrase stage {list(self.phrases)}, fuzzy remainder {self.remainder!r}"


def plan_text_stage(
    query: str, identifiers: Sequence[LegalIdentifier], *, index: str, path: str = "content"
) -> TextStagePlan:
    """Build the text stage of the hybrid search for a query.

    Without a statute identifier this is the ordinary fuzzy ``text`` operator,
    unchanged for every other query (FN, Grundbuch and Geschäftszahl queries
    included). With one, the stage is a ``compound.should`` of exact ``phrase``
    clauses on the citation spellings (``§ 1 TKG 2021``, ``§ 1 TKG``) and on the
    statute name (``TKG 2021``, ``TKG``), plus one fuzzy ``text`` clause on
    :func:`fuzzy_remainder` when anything is left of the question. No clause
    carries a bare number.
    """
    if not has_statute_citation(identifiers):
        return TextStagePlan(
            operator={"index": index, "text": {"query": query, "path": path, "fuzzy": dict(TEXT_FUZZY)}},
            mode="fuzzy",
        )
    phrases = _unique(
        variant
        for identifier in identifiers
        if identifier.kind == KIND_STATUTE
        for variant in (*identifier.variants, *identifier.name_variants)
    )
    should: List[Dict[str, Any]] = _phrase_clauses(phrases, path)
    remainder = fuzzy_remainder(query, identifiers)
    if remainder:
        should.append({"text": {"query": remainder, "path": path, "fuzzy": dict(TEXT_FUZZY)}})
    return TextStagePlan(
        operator={"index": index, "compound": {"should": should, "minimumShouldMatch": 1}},
        mode="statute",
        phrases=phrases,
        remainder=remainder,
    )


def semantic_guarantee(
    identifiers: Sequence[LegalIdentifier],
    vector_results: Sequence[Dict[str, Any]],
    count: int = SEMANTIC_GUARANTEE_COUNT,
) -> List[Dict[str, Any]]:
    """The vector results that keep a place in the fused window: the top ``count`` on a statute query.

    Empty for every other query, so :func:`merge_identifier_results` behaves as
    before for them.
    """
    if not has_statute_citation(identifiers):
        return []
    return list(vector_results[:count])


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
    *,
    guaranteed: Sequence[Dict[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """Pin exact hits ahead of the fused list and collapse copies.

    Order: exact hits that the normal search also found come first (they match
    the rest of the question too), then the other exact hits in search order,
    then the fused list in its own order. Copies, identified by
    :func:`copy_group_key`, keep their first representative; every other copy's
    source path is appended to ``metadata.locations``. Pinned representatives
    get ``metadata.exact_identifier_match = True`` and :data:`PINNED_SCORE` as
    their ``similarity``/``rrf_score``.

    ``guaranteed`` (the vector stage's top results on a statute query, see
    :func:`semantic_guarantee`) keep a place inside the window: those not already
    pinned follow the pinned block directly, in vector order, and the pinned
    block is cut to ``limit`` minus their number so they always fit; pinned hits
    that are among them lead the pinned block by vector rank. They keep their
    fused score and are marked ``metadata.semantic_guarantee = True``; every
    representative that is one of them carries ``metadata.vector_rank``
    (1-based). Empty ``guaranteed`` is the behaviour of every other query.

    Args:
        exact_hits: Results of the exact identifier query (search order).
        fused: The RRF-fused vector/text list (best first), not yet truncated.
        limit: Number of results to return.
        guaranteed: Vector results that must appear in the window, best first.

    Returns:
        At most ``limit`` result dicts; the metadata dicts are copies.
    """
    vector_ranks: Dict[Tuple[Any, ...], int] = {}
    for rank, doc in enumerate(guaranteed, start=1):
        vector_ranks.setdefault(_result_group_key(doc), rank)

    fused_ids = {str(doc.get("chunk_id")) for doc in fused}
    # Exact hits the normal search also found lead; among them the vector top
    # results (statute queries only) by vector rank, so the cited section's text
    # precedes its table-of-contents entry; then search order.
    pinned = sorted(
        enumerate(exact_hits),
        key=lambda item: (
            0 if str(item[1].get("chunk_id")) in fused_ids else 1,
            vector_ranks.get(_result_group_key(item[1]), len(vector_ranks) + 1),
            item[0],
        ),
    )
    pinned_docs = [doc for _, doc in pinned]
    pinned_keys = {_result_group_key(doc) for doc in pinned_docs}

    promoted: List[Dict[str, Any]] = []
    seen_guaranteed: set[Tuple[Any, ...]] = set()
    for doc in guaranteed:
        key = _result_group_key(doc)
        if key in seen_guaranteed:
            continue
        seen_guaranteed.add(key)
        if key not in pinned_keys:
            promoted.append(doc)
    cap = max(0, limit - len(promoted)) if promoted else len(pinned_docs)

    PINNED, GUARANTEED, FUSED = "pinned", "guaranteed", "fused"
    candidates: List[Tuple[Dict[str, Any], str]] = (
        [(doc, PINNED) for doc in pinned_docs[:cap]]
        + [(doc, GUARANTEED) for doc in promoted]
        + [(doc, PINNED) for doc in pinned_docs[cap:]]
        + [(doc, FUSED) for doc in fused]
    )

    representatives: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []
    collapsed = 0
    for doc, role in candidates:
        key = _result_group_key(doc)
        representative = representatives.get(key)
        if representative is None:
            representative = dict(doc)
            metadata = dict(doc.get("metadata") or {})
            metadata["locations"] = list(metadata.get("locations") or [])
            if role == PINNED:
                metadata["exact_identifier_match"] = True
                representative["similarity"] = PINNED_SCORE
                representative["rrf_score"] = PINNED_SCORE
            elif role == GUARANTEED:
                metadata["semantic_guarantee"] = True
            if key in vector_ranks:
                metadata["vector_rank"] = vector_ranks[key]
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
        "Identifier merge: %d exact hit(s), %d fused, %d guaranteed (%d promoted), %d cop(y/ies) collapsed, %d returned",
        len(exact_hits), len(fused), len(guaranteed), len(promoted), collapsed, min(len(ordered), limit),
    )
    return ordered[:limit]
