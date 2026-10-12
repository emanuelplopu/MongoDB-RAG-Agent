"""Document writing with the generator model and verbatim fact verification."""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Optional

from .config import SynthConfig
from .identities import _seed_int
from .llm import LLMClient
from .models import AreaSpec, CaseLedger, DocumentDraft, Fact, Firm, Identity, PlannedDocument, WrittenDocument
from .prompts import render
from .render import build_blocks, canonical_text, cost_table_rows
from .text_utils import contains_value, format_date_de

logger = logging.getLogger(__name__)

_LENGTHS: list[tuple[tuple[str, ...], str]] = [
    (("gutachten", "befund"), "1200 to 2500 words, organised in numbered chapters with headings"),
    (("klage", "beantwortung", "beschwerde", "stellungnahme", "antrag", "schriftsatz", "berufung", "einwendung"), "500 to 1300 words"),
    (("urteil", "bescheid", "beschluss", "vergleich", "zahlungsbefehl", "bewilligung"), "350 to 1300 words"),
    (("protokoll",), "400 to 1000 words"),
    (("vertrag", "angebot"), "400 to 1000 words"),
    (("aktenvermerk", "vermerk"), "60 to 220 words, terse"),
    (("rechnung", "abrechnung", "kostennote", "berechnung", "gehalt", "lohn"), "120 to 350 words plus line items"),
    (("email", "mail", "anzeige", "ruege", "rüge", "begehren", "benachrichtigung"), "60 to 250 words"),
]


def length_hint(doc: PlannedDocument) -> str:
    """Target length for a document type."""
    if doc.is_email:
        return "60 to 250 words"
    key = f"{doc.doc_type} {doc.title}".lower()
    for words, hint in _LENGTHS:
        if any(w in key for w in words):
            return hint
    return "150 to 450 words"


def _display(p: Optional[Identity]) -> str:
    if p is None:
        return ""
    name = p.full_name if p.kind == "natural" else p.name
    extra = f", {p.address}" if p.street else ""
    return f"{name} ({p.label}{extra})"


def _author(ledger: CaseLedger, doc: PlannedDocument) -> str:
    person = ledger.identity(doc.author_id)
    if person:
        return _display(person)
    proc = next((p for p in ledger.proceedings if p.id == doc.author_id), None)
    if proc:
        judge = ledger.identity(proc.judge_person_id) if proc.judge_person_id else None
        return f"{proc.court.name}, {proc.case_number}" + (f", {_display(judge)}" if judge else "")
    return doc.author_id


def _recipients(ledger: CaseLedger, doc: PlannedDocument) -> str:
    out = []
    for rid in doc.recipient_ids:
        person = ledger.identity(rid)
        if person:
            out.append(_display(person))
            continue
        proc = next((p for p in ledger.proceedings if p.id == rid), None)
        if proc:
            out.append(f"{proc.court.name} ({proc.case_number})")
    return "; ".join(out) or "keine (interner Vermerk)"


def _people(ledger: CaseLedger, known: set[str]) -> str:
    lines = []
    for p in ledger.identities:
        parts = [f"{p.id}: {p.full_name if p.kind == 'natural' else p.name}", p.label]
        if p.street:
            parts.append(p.address)
        if p.email:
            parts.append(p.email)
        if p.phone:
            parts.append(p.phone)
        if p.iban and p.role in ("mandant", "schuldner", "vermieter", "arbeitgeber", "bauunternehmen", "verantwortlicher"):
            parts.append(f"IBAN {p.iban}")
        if p.company_register_no:
            parts.append(p.company_register_no)
        if p.birth_date and p.role in ("mandant", "schuldner", "zeuge"):
            parts.append(f"geb. {format_date_de(p.birth_date)}")
        lines.append(" | ".join(parts))
    for proc in ledger.proceedings:
        judge = ledger.identity(proc.judge_person_id) if proc.judge_person_id else None
        number = f"Geschäftszahl {proc.case_number}" if proc.id in known else "noch keine Geschäftszahl vergeben"
        lines.append(f"{proc.id}: {proc.court.name}, {number} ({proc.label})"
                     + (f", zuständig: {judge.full_name}" if judge else ""))
    return "\n".join(lines)


def _firm_text(firm: Firm) -> str:
    staff = "; ".join(f"{s.id}: {s.full_name} ({s.label}, {s.email})" for s in firm.staff)
    return f"{firm.name}, {firm.address}, Tel. {firm.phone}, UID {firm.uid}. Team: {staff}"


def _cost_preview(doc: PlannedDocument) -> str:
    if not doc.cost_note:
        return ""
    return "\n".join(" | ".join(r) for r in cost_table_rows(doc.cost_note))


async def write_document(
    *,
    llm: LLMClient,
    cfg: SynthConfig,
    ledger: CaseLedger,
    firm: Firm,
    area: AreaSpec,
    doc: PlannedDocument,
) -> WrittenDocument:
    """Write one document; retry until every required value appears verbatim.

    Returns:
        The written document. ``missing_fact_ids`` lists required facts that were
        still missing after the last attempt (reported by validation).
    """
    required: list[Fact] = [ledger.fact(f) for f in doc.fact_ids]
    required_ids = set(doc.fact_ids)
    known = set(doc.known_proceeding_ids)
    hidden_az = {f"AZ_{p.id}" for p in ledger.proceedings if p.id not in known}
    context_facts = [f for f in ledger.facts if f.id not in required_ids and f.id not in hidden_az
                     and not f.id.startswith(("TITLE_", "COST_"))][:45]
    spec = next((t for t in area.document_types if t.type == doc.doc_type), None)
    earlier = [
        f"{d.id} ({format_date_de(d.date_iso)}, {d.title}): {d.summary}"
        for d in ledger.documents
        if (d.date_iso, d.id) < (doc.date_iso, doc.id)
    ][-14:]
    proc = next((p for p in ledger.proceedings if p.id == doc.proceeding_id), None)
    forbidden = ledger.fact(doc.filename_only_fact_id).value if doc.filename_only_fact_id else ""
    system = render("write_system", cfg.seeds.prompts_dir, length_hint=length_hint(doc))
    rng_seed = _seed_int(cfg.corpus.random_seed, ledger.case_id, doc.id, "email")

    feedback = ""
    draft: Optional[DocumentDraft] = None
    text = ""
    missing: list[str] = []
    attempts = 0
    for attempt in range(1, cfg.validation.max_write_attempts + 1):
        attempts = attempt
        user = render(
            "write_user",
            cfg.seeds.prompts_dir,
            doc=doc,
            case_title=ledger.title,
            doc_type_label=spec.label if spec else doc.doc_type,
            doc_date=format_date_de(doc.date_iso),
            author=_author(ledger, doc),
            recipients=_recipients(ledger, doc),
            type_guidance=spec.guidance if spec else "",
            proceeding=(f"{proc.court.name}, Geschäftszahl {proc.case_number} ({proc.label})" if proc and proc.id in known
                        else f"{proc.court.name} ({proc.label}); a case number does not exist yet: do not write any "
                             "Geschäftszahl" if proc else ""),
            cost_note=_cost_preview(doc),
            forbidden_phrase=forbidden,
            required=required,
            context_facts=context_facts,
            people=_people(ledger, known),
            firm=_firm_text(firm),
            case_summary=ledger.summary,
            earlier=earlier,
            style_notes=area.style_notes,
            retry_feedback=feedback,
        )
        draft = await llm.structured("generator", system, user, DocumentDraft, tag=f"write:{ledger.case_id}:{doc.id}:{attempt}")
        blocks, _ = build_blocks(draft, doc, ledger, random.Random(rng_seed))
        text = canonical_text(blocks)
        missing = [f.id for f in required if not contains_value(text, f.value)]
        forbidden_hit = bool(forbidden) and contains_value(text, forbidden)
        if not missing and not forbidden_hit:
            break
        problems = []
        if missing:
            problems.append(
                "These required values were missing or not written exactly; include each verbatim: "
                + "; ".join(f"{ledger.fact(m).label}: {ledger.fact(m).value}" for m in missing)
            )
        if forbidden_hit:
            problems.append(f'The forbidden phrase "{forbidden}" appeared; remove it everywhere.')
        feedback = "\n".join(problems)
        logger.info("%s/%s attempt %d rejected: %s", ledger.case_id, doc.id, attempt, feedback[:200])
    assert draft is not None
    return WrittenDocument(doc_id=doc.id, draft=draft, text=text, attempts=attempts, missing_fact_ids=missing)


def save_written(written: WrittenDocument, path: Path) -> None:
    """Persist a written document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(written.model_dump_json(indent=2), encoding="utf-8")


def load_written(path: Path) -> WrittenDocument:
    """Load a written document."""
    return WrittenDocument.model_validate_json(path.read_text(encoding="utf-8"))


def dump_prompt_preview(ledger: CaseLedger, doc: PlannedDocument) -> str:
    """Compact JSON of what a document is about (for ``--dry-run`` output)."""
    return json.dumps({"doc": doc.id, "title": doc.title, "facts": [ledger.fact(f).value for f in doc.fact_ids]}, ensure_ascii=False)
