"""Consistency checks across ledgers, written documents, rendered files and gold questions."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import fsutil
from .config import SynthConfig
from .models import CaseLedger, CaseManifest, GoldCase, WrittenDocument
from .render import extract_text
from .text_utils import contains_value

_COURT_NO = re.compile(r"^\d{1,2} [A-Za-z]{1,4} \d{1,4}/\d{2}[a-z]$")
_FOOTER = re.compile(r"Seite \d+ von \d+")
_AUTHORITY_NO = re.compile(r"^(\d{4}-0\.\d{3}\.\d{3}|MA 50-Schli \d{3}/\d{4})$")


def _eur(value: str) -> Decimal:
    return Decimal(value.replace("EUR ", "").replace(".", "").replace(",", "."))


class Report:
    """Collects errors, warnings and stats."""

    def __init__(self) -> None:  # noqa: D107
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: dict[str, Any] = {}

    def as_dict(self) -> dict[str, Any]:
        return {"ok": not self.errors, "errors": self.errors, "warnings": self.warnings, "stats": self.stats}


def validate_run(
    cfg: SynthConfig,
    ledgers: list[CaseLedger],
    written: dict[str, dict[str, WrittenDocument]],
    manifests: dict[str, CaseManifest],
    gold: list[GoldCase],
    corpus_root: Path,
    blocked_surnames: set[str],
) -> Report:
    """Run all checks and return a report."""
    r = Report()
    window = (date.fromisoformat(cfg.corpus.date_from), date.fromisoformat(cfg.corpus.date_to))
    formats: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    total_files = 0

    for led in ledgers:
        cid = led.case_id
        ids = [d.id for d in led.documents]
        if len(ids) != len(set(ids)):
            r.errors.append(f"{cid}: duplicate document ids")
        for p in led.proceedings:
            pattern = _AUTHORITY_NO if p.court.type == "Behoerde" else _COURT_NO
            if not pattern.match(p.case_number):
                r.errors.append(f"{cid}: malformed case number {p.case_number!r}")
        for d in led.documents:
            if not window[0] <= date.fromisoformat(d.date_iso) <= window[1]:
                r.warnings.append(f"{cid}/{d.id}: date {d.date_iso} outside corpus window")
            if d.cost_note:
                cn = d.cost_note
                expected = _eur(cn.fees_net) + _eur(cn.ust) + _eur(cn.court_fees) + _eur(cn.barauslagen)
                if abs(expected - _eur(cn.total)) > Decimal("0.02"):
                    r.errors.append(f"{cid}/{d.id}: cost note total {cn.total} != parts {expected}")
            w = written.get(cid, {}).get(d.id)
            if w is None:
                r.errors.append(f"{cid}/{d.id}: not written")
            elif w.missing_fact_ids:
                msg = f"{cid}/{d.id}: required facts missing after {w.attempts} attempts: {w.missing_fact_ids}"
                (r.errors if cfg.validation.require_all_facts_in_text else r.warnings).append(msg)
        used = {f for d in led.documents for f in d.fact_ids}
        unused = [f.id for f in led.facts if f.id not in used and not f.id.startswith("TITLE_")]
        if unused:
            r.warnings.append(f"{cid}: facts not assigned to any document: {unused}")

        manifest = manifests.get(cid)
        if manifest is None:
            r.errors.append(f"{cid}: not rendered")
            continue
        scan_facts: dict[str, set[str]] = {}
        for f in manifest.files:
            total_files += 1
            formats[f.format] += 1
            kinds[f.kind] += 1
            path = corpus_root / f.path
            if not fsutil.is_file(path):
                r.errors.append(f"{cid}: file missing on disk: {f.path}")
                continue
            if f.format == "scan_jpg":
                scan_facts.setdefault(f.of_doc_id, set()).update(f.fact_ids)
                continue
            if cfg.validation.check_rendered_text and f.kind == "primary":
                extracted = extract_text(path)
                if extracted is not None:
                    # page footers ("Seite 1 von 3") land mid-sentence in extracted PDF text; ingestion drops them
                    extracted = _FOOTER.sub(" ", extracted)
                    lost = [fid for fid in f.fact_ids if not contains_value(extracted, led.fact(fid).value)]
                    if lost:
                        r.errors.append(f"{cid}: {f.path}: values lost in rendering: {lost}")
        for d in led.documents:
            if d.resolved_format == "scan_jpg" and d.id in scan_facts:
                missing = [fid for fid in d.fact_ids if fid not in scan_facts[d.id]]
                if missing:
                    r.warnings.append(f"{cid}/{d.id}: facts not on any scanned page (truncated or split): {missing}")

        if blocked_surnames:
            # only flag a blocked surname where it is used as a person's name: after an academic title or a
            # legal role, optionally with first names in between (avoids nouns like "Angst" or "Korn")
            pattern = re.compile(
                r"(?:\b(?:Dr|Mag|MMag|DI|Ing|Prof)\.|\b(?:Rechtsanwalt|Rechtsanwältin|Richter|Richterin|Hofrat|Hofrätin"
                r"|Senatspräsident|Senatspräsidentin|Rechtspfleger|Rechtspflegerin))\s+(?:[A-ZÄÖÜ][\wäöüß-]+\s+){0,2}("
                + "|".join(map(re.escape, sorted(blocked_surnames))) + r")\b"
            )
            for f in manifest.files:
                m = pattern.search(f.text)
                if m:
                    r.errors.append(f"{cid}: {f.path}: contains blocked seed name {m.group(1)!r}")

    paths_by_case = {cid: {f.path: f for f in m.files} for cid, m in manifests.items()}
    all_files = {p: f for files in paths_by_case.values() for p, f in files.items()}
    type_counts: Counter[str] = Counter()
    for g in gold:
        type_counts[g.type] += 1
        if g.unanswerable:
            if g.gold_documents:
                r.errors.append(f"{g.id}: unanswerable question has gold documents")
            continue
        if not g.gold_documents:
            r.errors.append(f"{g.id}: no gold documents")
        for p in g.gold_documents:
            if not fsutil.is_file(corpus_root / p):
                r.errors.append(f"{g.id}: gold document missing: {p}")
        for s in g.gold_spans:
            rf = all_files.get(s.source)
            if rf is None or s.quote not in rf.text:
                r.errors.append(f"{g.id}: quote {s.quote!r} not found in {s.source}")
    ids = Counter(g.id for g in gold)
    for qid, n in ids.items():
        if n > 1:
            r.errors.append(f"duplicate question id {qid}")

    r.stats = {
        "cases": len(ledgers),
        "documents": sum(len(led.documents) for led in ledgers),
        "files": total_files,
        "files_by_format": dict(formats),
        "files_by_kind": dict(kinds),
        "facts": sum(len(led.facts) for led in ledgers),
        "questions": len(gold),
        "questions_by_type": dict(type_counts),
        "write_attempts": sum(w.attempts for docs in written.values() for w in docs.values()),
    }
    return r
