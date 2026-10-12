"""Corpus layout: render every written document of a case into the folder tree.

Layout::

    corpus/<Kanzlei>/Akten/<Aktenzahl Mandant gegen Gegner>/<Kategorie-Ordner>/<Datum Titel>.<ext>

Deliberate realism traps are added here: exact copies in other folders, and
draft versions in which one amount differs (distractors for questions).
"""

from __future__ import annotations

import logging
import random
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from . import fsutil
from .config import SynthConfig
from .identities import _seed_int
from .models import CaseLedger, CaseManifest, Firm, PlannedDocument, RenderedFile, WrittenDocument
from .render import (
    Block,
    EmailHeaders,
    build_blocks,
    canonical_text,
    convert_docx_to_doc,
    render_docx,
    render_eml,
    render_pdf,
    render_scan,
    render_txt,
)
from .text_utils import contains_value, format_eur, safe_filename

logger = logging.getLogger(__name__)


def firm_root(corpus_root: Path, firm: Firm) -> Path:
    """Root folder of the firm inside the corpus directory."""
    return corpus_root / safe_filename(firm.name)


def case_root(corpus_root: Path, firm: Firm, ledger: CaseLedger) -> Path:
    """Folder of one case file."""
    return firm_root(corpus_root, firm) / "Akten" / ledger.folder_name


def _facts_in(text: str, ledger: CaseLedger) -> list[str]:
    return [f.id for f in ledger.facts if f.value and contains_value(text, f.value)]


def _set_mtime(path: Path, iso_date: str, rng: random.Random) -> None:
    d = datetime.fromisoformat(iso_date).replace(hour=rng.randint(8, 18), minute=rng.randint(0, 59))
    fsutil.utime(path, d.timestamp())


def _author_name(ledger: CaseLedger, doc: PlannedDocument, firm: Firm) -> str:
    person = ledger.identity(doc.author_id)
    if person:
        return person.full_name if person.kind == "natural" else person.name
    proc = next((p for p in ledger.proceedings if p.id == doc.author_id), None)
    return proc.court.name if proc else firm.name


def _render_format(
    fmt: str,
    blocks: list[Block],
    headers: Optional[EmailHeaders],
    path: Path,
    cfg: SynthConfig,
    *,
    title: str,
    author: str,
    doc_date: str,
    rng: random.Random,
) -> list[tuple[Path, str, int, Optional[int], str]]:
    """Render blocks in ``fmt``. Returns ``(path, text, pages, page_index, note)`` per file."""
    text = canonical_text(blocks)
    if fmt == "docx":
        render_docx(blocks, path, title=title, author=author, doc_date=doc_date)
        return [(path, text, 1, None, "")]
    if fmt == "pdf":
        pages = render_pdf(blocks, path, cfg.corpus.pdf, title=title, author=author)
        return [(path, text, pages, None, "")]
    if fmt == "txt":
        render_txt(blocks, path)
        return [(path, text, 1, None, "")]
    if fmt == "eml":
        if headers is None:
            txt = path.with_suffix(".txt")
            render_txt(blocks, txt)
            return [(txt, text, 1, None, "eml requested for a non-email document; wrote txt")]
        render_eml(blocks, headers, path)
        return [(path, text, 1, None, "")]
    if fmt == "doc":
        with tempfile.TemporaryDirectory() as tmp:
            tmp_docx = Path(tmp) / (path.stem + ".docx")
            render_docx(blocks, tmp_docx, title=title, author=author, doc_date=doc_date)
            if convert_docx_to_doc(tmp_docx, path):
                return [(path, text, 1, None, "")]
            if cfg.corpus.doc_fallback == "skip":
                return []
            fallback = path.with_suffix(".docx")
            fsutil.copy2(tmp_docx, fallback)
            return [(fallback, text, 1, None, "legacy .doc requested; LibreOffice not available, wrote .docx")]
    if fmt == "scan_jpg":
        pages = render_scan(blocks, path.with_suffix(""), cfg.corpus.pdf, cfg.corpus.scan, rng, title=title)
        n = len(pages)
        return [(p, page_text, n, (i + 1 if n > 1 else None), "scan") for i, (p, page_text) in enumerate(pages)]
    raise ValueError(f"unknown format {fmt}")


def _alter_amount(value: str, rng: random.Random) -> Optional[str]:
    raw = value.replace("EUR ", "").replace(".", "").replace(",", ".")
    try:
        amount = Decimal(raw)
    except Exception:  # noqa: BLE001 - not an amount
        return None
    factor = Decimal(str(rng.choice([0.85, 0.9, 1.1, 1.2])))
    altered = format_eur(amount * factor)
    return altered if altered != value else None


def render_case(
    cfg: SynthConfig,
    ledger: CaseLedger,
    firm: Firm,
    written: dict[str, WrittenDocument],
    corpus_root: Path,
) -> tuple[CaseManifest, list[str]]:
    """Render all documents of a case. Returns ``(manifest, warnings)``."""
    warnings: list[str] = []
    root = case_root(corpus_root, firm, ledger)
    if fsutil.is_dir(root):
        fsutil.rmtree(root)
    rng = random.Random(_seed_int(cfg.corpus.random_seed, ledger.case_id, "layout"))
    files: list[RenderedFile] = []

    def rel(p: Path) -> str:
        return p.relative_to(corpus_root).as_posix()

    for doc in ledger.documents:
        w = written.get(doc.id)
        if w is None:
            warnings.append(f"{doc.id}: not written, skipped")
            continue
        email_rng = random.Random(_seed_int(cfg.corpus.random_seed, ledger.case_id, doc.id, "email"))
        blocks, headers = build_blocks(w.draft, doc, ledger, email_rng)
        folder = root / cfg.corpus.folders.get(doc.category, doc.category)
        path = folder / doc.filename
        title = "" if doc.filename_only_fact_id else w.draft.title
        author = _author_name(ledger, doc, firm)
        scan_rng = random.Random(_seed_int(cfg.corpus.random_seed, ledger.case_id, doc.id, "scan"))
        produced = _render_format(doc.resolved_format, blocks, headers, path, cfg, title=title, author=author,
                                  doc_date=doc.date_iso, rng=scan_rng)
        primary: list[RenderedFile] = []
        for out_path, text, pages, page_index, note in produced:
            fmt = "scan_jpg" if out_path.suffix == ".jpg" else out_path.suffix.lstrip(".")
            _set_mtime(out_path, doc.date_iso, rng)
            rf = RenderedFile(path=rel(out_path), format=fmt, kind="primary", of_doc_id=doc.id, pages=pages,
                              page_index=page_index, note=note, text=text, fact_ids=_facts_in(text, ledger))
            primary.append(rf)
            if note and "LibreOffice" in note:
                warnings.append(f"{doc.id}: {note}")
        files.extend(primary)

        if primary and rng.random() < cfg.corpus.duplicates.exact_copy_rate:
            copy_folder = root / rng.choice(cfg.corpus.duplicates.copy_folders)
            for rf in primary:
                src = corpus_root / rf.path
                dst = copy_folder / src.name
                fsutil.copy2(src, dst)
                files.append(rf.model_copy(update={"path": rel(dst), "kind": "copy", "note": f"exact copy of {rf.path}"}))

        if (primary and doc.resolved_format in ("docx", "pdf") and not doc.is_email
                and rng.random() < cfg.corpus.duplicates.version_rate):
            amount_facts = [ledger.fact(f) for f in doc.fact_ids if ledger.fact(f).kind == "amount" and ledger.fact(f).origin == "planner"]
            amount_facts = [f for f in amount_facts if contains_value(primary[0].text, f.value)]
            if amount_facts:
                target = rng.choice(amount_facts)
                altered = _alter_amount(target.value, rng)
                if altered:
                    v_blocks = [Block("header", "ENTWURF")] + [
                        Block(b.kind, b.text.replace(target.value, altered), [[c.replace(target.value, altered) for c in r] for r in b.rows])
                        for b in blocks
                    ]
                    v_path = path.with_name(f"{path.stem} - Entwurf{path.suffix}")
                    for out_path, text, pages, page_index, note in _render_format(
                        doc.resolved_format, v_blocks, headers, v_path, cfg, title=f"ENTWURF {title}".strip(),
                        author=author, doc_date=doc.date_iso, rng=scan_rng,
                    ):
                        _set_mtime(out_path, doc.date_iso, rng)
                        files.append(RenderedFile(
                            path=rel(out_path), format=out_path.suffix.lstrip("."), kind="version", of_doc_id=doc.id,
                            pages=pages, note=f"draft: {target.label} changed from {target.value} to {altered}",
                            text=text, fact_ids=_facts_in(text, ledger), distractor_fact_id=target.id, distractor_value=altered,
                        ))
    manifest = CaseManifest(case_id=ledger.case_id, area_id=ledger.area_id, folder=rel(root), files=files)
    return manifest, warnings
