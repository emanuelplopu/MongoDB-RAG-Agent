"""Case planning: orchestrator model output plus deterministic post-processing.

The orchestrator returns a :class:`~.models.PlanOutput`. Everything that must be
exact is then done in code: identities, case numbers, display formats of
amounts and dates, placeholder substitution, cost-note arithmetic, output
formats, file names and reference repair. The result is the
:class:`~.models.CaseLedger`, the single source of truth for later stages.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR, SynthConfig
from .identities import IdentityFactory, _seed_int
from .llm import LLMClient
from .models import (
    AreaSpec,
    CaseLedger,
    CaseSeed,
    CaseSeedDraftOutput,
    CostLineView,
    CostNoteView,
    Court,
    EventOutput,
    Fact,
    Firm,
    Identity,
    PlannedDocument,
    PlanOutput,
    Proceeding,
)
from .prompts import render
from .ris import RisDecision
from .text_utils import format_date_de, format_eur, safe_filename

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([PXKV]\d+)\}")
_CHECK_LETTERS = "abcdfghikmnpstvwxyz"
_EXT = {"docx": "docx", "pdf": "pdf", "doc": "doc", "eml": "eml", "scan_jpg": "jpg", "txt": "txt"}
_LAWYER_ROLES = {"gegenvertreter"}
_JUDGE_ROLES = {"richter", "rechtspfleger"}


def _valid_iso(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def load_service_mapping() -> list[dict[str, Any]]:
    """RATG service-to-post mapping from reference data, if present."""
    path = DATA_DIR / "reference" / "austrian_procedure.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("ratg_service_mapping", []))


def build_identities(area: AreaSpec, seed: CaseSeed, factory: IdentityFactory) -> list[Identity]:
    """Create the fixed identities for every role of the area (ids ``P1``..)."""
    out: list[Identity] = []
    for i, role in enumerate(area.roles, start=1):
        hint = seed.role_hints.get(role.role)
        if role.role in _LAWYER_ROLES:
            titles = ["Dr.", "Mag.", "MMag.", "Mag. Dr."]
        elif role.role in _JUDGE_ROLES:
            titles = ["Mag.", "Dr.", "Mag. Dr."] if role.role == "richter" else [""]
        elif role.role in {"sachverstaendiger", "privatgutachter"}:
            titles = ["Ing.", "DI", "Baumeister Ing.", "DI Dr."]
        else:
            titles = None
        kind = role.kind
        if hint and hint.name_word and kind == "natural":
            kind = "legal"
        out.append(
            factory.person(
                f"P{i}",
                role.role,
                role.label,
                scope=seed.id,
                kind=kind,
                title_pool=titles,
                vienna=bool(hint.vienna) if hint and hint.vienna is not None
                else role.role in _LAWYER_ROLES | _JUDGE_ROLES | {"dsb_referent"},
                gender=hint.gender if hint else None,
                name_word=hint.name_word if hint else None,
                legal_form=hint.legal_form if hint else None,
                postcode=hint.postcode if hint else None,
            )
        )
    return out


def _case_number(court: Court, register: str, year: int, rng: random.Random) -> str:
    if court.type == "Behoerde":
        if court.id == "dsb":
            return f"{year}-0.{rng.randint(100, 999)}.{rng.randint(100, 999)}"
        return f"MA 50-Schli {rng.randint(100, 999)}/{year}"
    dept = rng.randint(1, 10) if register == "Ob" else rng.randint(1, 64)
    return f"{dept} {register} {rng.randint(12, 980)}/{str(year)[-2:]}{rng.choice(_CHECK_LETTERS)}"


def _person_display(p: Identity) -> str:
    return p.full_name if p.kind == "natural" else p.name


class _Substituter:
    """Replaces {P1}/{X1}/{K1}/{V1} placeholders with names and case numbers."""

    def __init__(self, persons: dict[str, Identity], proceedings: dict[str, Proceeding]):  # noqa: D107
        self.persons = persons
        self.proceedings = proceedings
        self.unknown: set[str] = set()

    def __call__(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            if key in self.persons:
                return _person_display(self.persons[key])
            if key in self.proceedings:
                return self.proceedings[key].case_number
            self.unknown.add(key)
            return m.group(0)

        return _PLACEHOLDER.sub(repl, text or "")


def _weighted_choice(rng: random.Random, weights: dict[str, float], allowed: Optional[list[str]] = None) -> str:
    items = [(k, w) for k, w in weights.items() if (allowed is None or k in allowed)]
    if allowed and not any(w > 0 for _, w in items):
        items = [(k, 1.0) for k in allowed]
    keys, ws = zip(*items)
    return rng.choices(list(keys), weights=list(ws), k=1)[0]


def _fix_ggg_post(post: str, description: str) -> str:
    """Correct obvious GGG post mix-ups (e.g. an Exekution fee booked as appeal fee TP2)."""
    text = description.lower()
    suffix = "_ARB" if post.upper().endswith("_ARB") else ""
    if "exekution" in text and not post.upper().startswith("TP4"):
        return f"TP4{suffix}"
    if "berufung" in text and post.upper().startswith("TP1"):
        return f"TP2{suffix}"
    if "revision" in text and post.upper().startswith(("TP1", "TP2")):
        return f"TP3{suffix}"
    return post


def compute_cost_note(engine: Any, plan_items: list[Any], warnings: list[str], doc_id: str) -> Optional[CostNoteView]:
    """Run the fee engine on the planned items; failing items are dropped with a warning."""
    from .fees import CostItemRequest, FeeTableError

    requests = []
    for it in plan_items:
        if it.kind == "ggg":
            fixed = _fix_ggg_post(it.post, it.description)
            if fixed != it.post:
                warnings.append(f"{doc_id}: GGG post {it.post} for {it.description[:40]!r} corrected to {fixed}")
                it = it.model_copy(update={"post": fixed})
        try:
            requests.append(
                CostItemRequest(
                    kind=it.kind,
                    post=it.post.replace(" ", "").upper() if it.kind != "barauslage" else it.post,
                    service_date=date.fromisoformat(it.service_date_iso),
                    bemessungsgrundlage=Decimal(str(round(it.bemessungsgrundlage_eur, 2))),
                    description=it.description,
                    einheitssatz=it.einheitssatz,
                    einheitssatz_factor=min(4, max(1, int(it.einheitssatz_factor or 1))) if it.kind == "ratg" else 1,
                    erv=it.erv,
                    amount=Decimal(str(round(it.barauslage_amount_eur, 2))) if it.barauslage_amount_eur is not None else None,
                )
            )
        except (ValueError, TypeError) as exc:
            warnings.append(f"{doc_id}: dropped cost item {it.description!r}: {exc}")
    usable = []
    for req in requests:
        try:
            engine.cost_note([req])
            usable.append(req)
        except (FeeTableError, KeyError, ValueError) as exc:
            warnings.append(f"{doc_id}: fee engine rejected {req.kind} {req.post} on {req.service_date}: {exc}")
    if not usable:
        return None
    note = engine.cost_note(usable)
    return CostNoteView(
        lines=[
            CostLineView(
                description=line.description,
                post=line.post,
                service_date=format_date_de(line.service_date.isoformat()),
                base=format_eur(line.base_amount),
                einheitssatz=format_eur(line.einheitssatz_amount),
                erv=format_eur(line.erv_amount),
                net=format_eur(line.net_amount),
            )
            for line in note.lines
        ],
        fees_net=format_eur(note.fees_net),
        ust=format_eur(note.ust_amount),
        court_fees=format_eur(note.court_fees),
        barauslagen=format_eur(note.barauslagen),
        total=format_eur(note.total),
        table_ids=sorted({line.table_id for line in note.lines}),
    )


def postprocess_plan(
    plan: PlanOutput,
    *,
    area: AreaSpec,
    seed: CaseSeed,
    identities: list[Identity],
    firm: Firm,
    courts: dict[str, Court],
    cfg: SynthConfig,
    factory: IdentityFactory,
    fee_engine: Any,
    case_index: int,
    ris_sources: list[str],
) -> tuple[CaseLedger, list[str]]:
    """Turn the orchestrator plan into a consistent ledger. Returns ``(ledger, warnings)``."""
    warnings: list[str] = []
    rng = random.Random(_seed_int(cfg.corpus.random_seed, seed.id, "postprocess"))

    persons: dict[str, Identity] = {p.id: p for p in identities}
    for s in firm.staff:
        persons[s.id] = s
    for j, extra in enumerate(plan.extra_persons, start=1):
        pid = extra.id if extra.id.startswith("X") and extra.id not in persons else f"X{j}"
        persons[pid] = factory.person(
            pid, extra.role, extra.label, scope=seed.id, kind=extra.kind,
            gender=None if extra.gender == "unspecified" else extra.gender, description=extra.description,
        )

    proceedings: dict[str, Proceeding] = {}
    allowed_courts = {c: courts[c] for c in area.courts if c in courts}
    for p in plan.proceedings:
        court = allowed_courts.get(p.court_id) or courts.get(p.court_id)
        if court is None:
            court = next(iter(allowed_courts.values()))
            warnings.append(f"proceeding {p.id}: unknown court {p.court_id!r}, using {court.id}")
        judge = p.judge_person_id if p.judge_person_id in persons else None
        proceedings[p.id] = Proceeding(
            id=p.id, court=court, court_register=p.court_register, year=p.year, label=p.label,
            case_number=_case_number(court, p.court_register, p.year, rng), judge_person_id=judge,
        )
    sub = _Substituter(persons, proceedings)
    for proc in proceedings.values():
        proc.label = sub(proc.label)

    date_from, date_to = date.fromisoformat(cfg.corpus.date_from), date.fromisoformat(cfg.corpus.date_to)
    facts: dict[str, Fact] = {}
    for f in plan.facts:
        fid = f.id if f.id not in facts else f"{f.id}_{len(facts)}"
        kind, value, amount, d_iso = f.kind, None, None, None
        if kind == "amount" and f.amount_eur is not None:
            amount, value = float(f.amount_eur), format_eur(f.amount_eur)
        elif kind in ("date", "deadline") and _valid_iso(f.date_iso):
            d_iso, value = f.date_iso, format_date_de(f.date_iso)
        else:
            if kind in ("amount", "date", "deadline"):
                warnings.append(f"fact {fid}: kind {kind} without a typed value; kept as text")
                kind = "other"
            value = sub(f.text or f.label)
        facts[fid] = Fact(id=fid, kind=kind, label=sub(f.label), value=value, description=sub(f.description),
                          amount_eur=amount, date_iso=d_iso)
    for p in proceedings.values():
        facts[f"AZ_{p.id}"] = Fact(id=f"AZ_{p.id}", kind="reference", label=f"Geschäftszahl {sub(p.label)}",
                                   value=p.case_number, description=f"{p.court.name}", origin="code")

    type_specs = {t.type: t for t in area.document_types}
    documents: list[PlannedDocument] = []
    known_doc_ids = {d.id for d in plan.documents}
    for d in plan.documents:
        doc = PlannedDocument(**d.model_dump())
        doc.title, doc.summary = sub(doc.title), sub(doc.summary)
        if not _valid_iso(doc.date_iso):
            warnings.append(f"{doc.id}: invalid date {doc.date_iso!r}, using case start")
            doc.date_iso = (seed.start_date or cfg.corpus.date_from)
        if not (date_from <= date.fromisoformat(doc.date_iso) <= date_to):
            warnings.append(f"{doc.id}: date {doc.date_iso} outside corpus window")
        if doc.author_id not in persons and doc.author_id not in proceedings:
            warnings.append(f"{doc.id}: unknown author {doc.author_id!r}, using K1")
            doc.author_id = "K1"
        doc.recipient_ids = [r for r in doc.recipient_ids if r in persons or r in proceedings]
        if doc.proceeding_id and doc.proceeding_id not in proceedings:
            warnings.append(f"{doc.id}: unknown proceeding {doc.proceeding_id!r}")
            doc.proceeding_id = None
        doc.reference_doc_ids = [r for r in doc.reference_doc_ids if r in known_doc_ids]
        dropped = [f for f in doc.fact_ids if f not in facts]
        if dropped:
            warnings.append(f"{doc.id}: dropped unknown facts {dropped}")
        doc.fact_ids = [f for f in dict.fromkeys(doc.fact_ids) if f in facts]
        if doc.proceeding_id and doc.category in ("gericht", "schriftsatz", "kosten"):
            az = f"AZ_{doc.proceeding_id}"
            if az not in doc.fact_ids:
                doc.fact_ids.append(az)  # removed again below if the case number does not exist yet

        spec = type_specs.get(doc.doc_type)
        if doc.is_email or (spec and spec.is_email and doc.format in ("auto", "eml")):
            doc.is_email = True
            doc.resolved_format = _weighted_choice(rng, dict(cfg.corpus.email_format_weights))  # type: ignore[assignment]
        elif doc.format not in ("auto", "eml"):
            doc.resolved_format = doc.format  # type: ignore[assignment]
        else:
            allowed = [f for f in (spec.formats if spec else []) if f != "eml"] or None
            weights = {k: v for k, v in cfg.corpus.format_weights.items() if k != "eml"}
            doc.resolved_format = _weighted_choice(rng, weights, allowed)  # type: ignore[assignment]

        if (doc.cost_items or (spec and spec.is_cost_note)) and fee_engine is not None:
            view = compute_cost_note(fee_engine, doc.cost_items, warnings, doc.id) if doc.cost_items else None
            if view is None:
                warnings.append(f"{doc.id}: cost note without computable items")
            else:
                doc.cost_note = view
                fid = f"COST_TOTAL_{doc.id}"
                total = Decimal(view.total.replace("EUR ", "").replace(".", "").replace(",", "."))
                facts[fid] = Fact(id=fid, kind="amount", label=f"Gesamtbetrag {doc.title}", value=view.total,
                                  amount_eur=float(total), origin="code")
                doc.fact_ids.append(fid)
        documents.append(doc)

    documents.sort(key=lambda x: (x.date_iso, x.id))
    # A case number exists only once the court or authority has acted: from its first own document on.
    # Initiating pleadings and anything earlier must not mention it.
    for pid in proceedings:
        own = [d.date_iso for d in documents if d.author_id == pid]
        related = [d.date_iso for d in documents if d.proceeding_id == pid]
        known_from = min(own) if own else (min(related) if related else "9999-12-31")
        for d in documents:
            if d.author_id == pid or d.date_iso > known_from:
                d.known_proceeding_ids.append(pid)
    for d in documents:
        hidden = {f"AZ_{pid}" for pid in proceedings if pid not in d.known_proceeding_ids}
        if hidden & set(d.fact_ids):
            d.fact_ids = [f for f in d.fact_ids if f not in hidden]
    candidates = [d for d in documents if not d.is_email and d.cost_note is None and d.resolved_format != "scan_jpg"]
    for d in candidates:
        if rng.random() < cfg.corpus.filename_only_fact_rate:
            fid = f"TITLE_{d.id}"
            facts[fid] = Fact(id=fid, kind="other", label=f"Dokumenttitel von {d.id}", value=d.title,
                              description="Steht nur im Dateinamen, nicht im Dokumenttext", origin="code")
            d.filename_only_fact_id = fid

    used: dict[str, set[str]] = {}
    for d in documents:
        folder = d.category
        stem = safe_filename(f"{d.date_iso} {d.title}", max_len=90)
        names = used.setdefault(folder, set())
        name, n = stem, 2
        while name.lower() in names:
            name, n = f"{stem} ({n})", n + 1
        names.add(name.lower())
        d.filename = f"{name}.{_EXT[d.resolved_format]}"

    events = [
        EventOutput(id=e.id, date_iso=e.date_iso, label=sub(e.label), fact_ids=[f for f in e.fact_ids if f in facts])
        for e in sorted(plan.events, key=lambda e: e.date_iso)
    ]
    referenced = {f for d in documents for f in d.fact_ids}
    unused = [f for f in facts if f not in referenced and not f.startswith("TITLE_")]
    if unused:
        warnings.append(f"facts not used by any document: {unused}")
    if sub.unknown:
        warnings.append(f"unknown placeholders: {sorted(sub.unknown)}")

    client = persons.get(plan.client_person_id) or persons["P1"]
    opponent_role = area.roles[1].role if len(area.roles) > 1 else None
    opponent = next((p for p in identities if p.role == opponent_role), None)
    year = date.fromisoformat(documents[0].date_iso).year if documents else date_from.year
    internal_no = f"{cfg.firm.internal_file_prefix}-{year}-{101 + case_index * 17:04d}"

    def short(p: Optional[Identity]) -> str:
        if p is None:
            return ""
        return p.name if p.kind == "legal" else p.name.split(" ")[-1]

    folder_name = safe_filename(f"{internal_no} {short(client)}" + (f" gegen {short(opponent)}" if opponent else ""), max_len=64)
    ledger = CaseLedger(
        case_id=seed.id,
        area_id=area.id,
        seed=seed,
        internal_file_number=internal_no,
        folder_name=folder_name,
        title=sub(plan.title),
        summary=sub(plan.summary),
        streitwert_eur=plan.streitwert_eur,
        client_person_id=client.id,
        identities=list(persons.values()),
        proceedings=list(proceedings.values()),
        facts=list(facts.values()),
        events=events,
        documents=documents,
        outcome=sub(plan.outcome),
        ris_sources=ris_sources,
    )
    return ledger, warnings


def _identity_brief(p: Identity) -> dict[str, Any]:
    out: dict[str, Any] = {"id": p.id, "role": p.role, "label": p.label, "kind": p.kind, "name": _person_display(p)}
    if p.street:
        out["address"] = p.address
    if p.birth_date and p.kind == "natural" and p.role not in ("richter", "rechtspfleger"):
        out["birth_date"] = p.birth_date
    if p.company_register_no:
        out["firmenbuch"] = p.company_register_no
    if p.iban and p.role in ("mandant", "schuldner", "vermieter", "arbeitgeber", "bauunternehmen"):
        out["iban"] = p.iban
    if p.email:
        out["email"] = p.email
    return out


@dataclass
class PlanPrompt:
    """Everything needed to call the orchestrator for one case and post-process its answer."""

    seed: CaseSeed
    area: AreaSpec
    identities: list[Identity]
    system: str
    user: str
    decisions: list[RisDecision]


def prepare_plan(
    *,
    cfg: SynthConfig,
    area: AreaSpec,
    seed: CaseSeed,
    firm: Firm,
    courts: dict[str, Court],
    factory: IdentityFactory,
    decisions: list[RisDecision],
) -> PlanPrompt:
    """Assign identities (sequential, keeps names unique) and build the planner prompts."""
    rng = random.Random(_seed_int(cfg.corpus.random_seed, seed.id, "plan"))
    identities = build_identities(area, seed, factory)
    target = seed.documents_target or rng.randint(cfg.corpus.documents_per_case.min, cfg.corpus.documents_per_case.max)
    lo, hi = area.streitwert_range
    streitwert = seed.streitwert or round(rng.uniform(lo, hi), 2)
    date_from, date_to = date.fromisoformat(cfg.corpus.date_from), date.fromisoformat(cfg.corpus.date_to)
    start = seed.start_date or (date_from + timedelta(days=rng.randint(0, max(1, (date_to - date_from).days // 3)))).isoformat()

    budget = cfg.seeds.ris.max_seed_chars
    trimmed: list[dict[str, str]] = []
    for d in decisions:
        if budget <= 500:
            break
        text = d.text[:budget]
        budget -= len(text)
        trimmed.append({"citation": d.citation, "text": text})

    area_brief = area.model_dump(exclude={"ris_queries", "streitwert_range"})
    seed_brief = seed.model_dump(exclude={"role_hints", "ris_geschaeftszahlen", "use_ris_search"})
    firm_brief = {
        "name": firm.name, "address": firm.address, "email_domain": firm.email_domain,
        "staff": [_identity_brief(s) for s in firm.staff],
    }
    user = render(
        "plan_user",
        cfg.seeds.prompts_dir,
        area_json=json.dumps(area_brief, ensure_ascii=False, indent=1),
        seed_json=json.dumps(seed_brief, ensure_ascii=False, indent=1),
        firm_json=json.dumps(firm_brief, ensure_ascii=False, indent=1),
        identities_json=json.dumps([_identity_brief(p) for p in identities], ensure_ascii=False, indent=1),
        courts_json=json.dumps([courts[c].model_dump() for c in area.courts if c in courts], ensure_ascii=False, indent=1),
        service_mapping_json=json.dumps(load_service_mapping(), ensure_ascii=False, indent=1),
        date_from=cfg.corpus.date_from,
        date_to=cfg.corpus.date_to,
        start_date=start,
        documents_target=target,
        streitwert=f"{streitwert:.2f}",
        client_id=next(p.id for p in identities if p.role == seed.client_role),
        decisions=trimmed,
    )
    system = render("plan_system", cfg.seeds.prompts_dir)
    return PlanPrompt(seed=seed, area=area, identities=identities, system=system, user=user, decisions=decisions)


async def plan_case(
    *,
    llm: LLMClient,
    cfg: SynthConfig,
    area: AreaSpec,
    seed: CaseSeed,
    firm: Firm,
    courts: dict[str, Court],
    factory: IdentityFactory,
    fee_engine: Any,
    case_index: int,
    decisions: list[RisDecision],
) -> tuple[CaseLedger, list[str]]:
    """Plan one case: prompt the orchestrator, then post-process into a ledger."""
    prompt = prepare_plan(cfg=cfg, area=area, seed=seed, firm=firm, courts=courts, factory=factory, decisions=decisions)
    plan = await llm.structured("orchestrator", prompt.system, prompt.user, PlanOutput, tag=f"plan:{seed.id}")
    return postprocess_plan(
        plan, area=area, seed=seed, identities=prompt.identities, firm=firm, courts=courts, cfg=cfg,
        factory=factory, fee_engine=fee_engine, case_index=case_index,
        ris_sources=[d.citation for d in decisions],
    )


async def generate_case_seed(
    *, llm: LLMClient, cfg: SynthConfig, area: AreaSpec, existing: list[CaseSeed], new_id: str
) -> CaseSeed:
    """Ask the orchestrator for a new scenario in ``area`` that differs from ``existing``."""
    user = render(
        "case_seed_user",
        cfg.seeds.prompts_dir,
        area_json=json.dumps(area.model_dump(exclude={"ris_queries", "document_types"}), ensure_ascii=False, indent=1),
        existing=[f"{c.title}: {c.description[:300]}" for c in existing],
        streitwert_min=area.streitwert_range[0],
        streitwert_max=area.streitwert_range[1],
        date_from=cfg.corpus.date_from,
        date_to=cfg.corpus.date_to,
    )
    draft = await llm.structured("orchestrator", render("case_seed_system", cfg.seeds.prompts_dir), user,
                                 CaseSeedDraftOutput, tag=f"seed:{new_id}")
    start = draft.start_date_iso if _valid_iso(draft.start_date_iso) else None
    return CaseSeed(
        id=new_id, area=area.id, title=draft.title, description=draft.description, start_date=start,
        streitwert=round(draft.streitwert_eur, 2), extra_instructions=draft.extra_instructions,
        tags=draft.tags + ["generated"],
    )


def save_ledger(ledger: CaseLedger, path: Path) -> None:
    """Write the ledger as pretty JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")


def load_ledger(path: Path) -> CaseLedger:
    """Read a ledger written by :func:`save_ledger`."""
    return CaseLedger.model_validate_json(path.read_text(encoding="utf-8"))
