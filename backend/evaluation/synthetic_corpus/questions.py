"""Gold question generation.

The orchestrator only phrases questions. What counts as correct is derived by
code from the ledger and the rendered files: which documents contain the facts
(gold documents), where exactly (text-quote selectors), which values an answer
must include, and which draft values it must not include.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .config import SynthConfig
from .identities import _seed_int
from .llm import LLMClient
from .models import (
    CaseLedger,
    CaseManifest,
    Fact,
    GoldCase,
    GoldSpan,
    Identity,
    QuestionBatchOutput,
    RenderedFile,
    UnanswerableBatchOutput,
)
from .prompts import render
from .text_utils import find_quote, format_date_de

logger = logging.getLogger(__name__)

_EXACT_KINDS = {"amount", "date", "reference", "deadline"}
_TEXT_KINDS = {"statement", "party", "other", "legal_basis"}

_INSTRUCTIONS = {
    "single_fact": "Ask for this single fact.",
    "exact_token": "Ask for this exact value (amount, date or reference number); the answer must give it exactly.",
    "multi_doc": "Ask one question whose complete answer needs both facts; they come from different documents.",
    "timeline": "Ask for both dates and which event came first (or the time between them).",
    "filename_only": "The colleague refers to the document by its title (which appears only in the file name, not in the text). Ask for the other fact from that document and mention the title.",
    "scan_ocr": "Ask for this fact; it is only contained in a scanned paper document.",
}


@dataclass
class Selection:
    """Facts chosen for one question."""

    type: str
    facts: list[Fact]
    instruction: str
    files: list[RenderedFile] = field(default_factory=list)

    @property
    def documents(self) -> str:
        return "; ".join(sorted({f.path.rsplit("/", 1)[-1] for f in self.files}))


class FactIndex:
    """Where each fact occurs in the rendered files of a case."""

    def __init__(self, ledger: CaseLedger, manifest: CaseManifest):  # noqa: D107
        self.ledger = ledger
        self.primary: dict[str, list[RenderedFile]] = defaultdict(list)
        self.copies: dict[str, list[RenderedFile]] = defaultdict(list)
        self.versions: dict[str, list[RenderedFile]] = defaultdict(list)
        self.by_doc: dict[str, list[RenderedFile]] = defaultdict(list)
        for f in manifest.files:
            self.by_doc[f.of_doc_id].append(f)
            target = {"primary": self.primary, "copy": self.copies, "version": self.versions}[f.kind]
            for fid in f.fact_ids:
                target[fid].append(f)
            if f.kind == "version" and f.distractor_fact_id and f not in self.versions[f.distractor_fact_id]:
                # the altered value replaced the fact's value, so index the draft under that fact too
                self.versions[f.distractor_fact_id].append(f)

    def planned_files(self, fact_id: str) -> list[RenderedFile]:
        """Primary files of documents the plan assigned the fact to (falls back to any file with the value)."""
        planned = {d.id for d in self.ledger.documents if fact_id in d.fact_ids}
        files = [f for f in self.primary.get(fact_id, []) if f.of_doc_id in planned]
        return files or self.primary.get(fact_id, [])

    def primary_docs(self, fact_id: str) -> set[str]:
        return {f.of_doc_id for f in self.primary.get(fact_id, [])}

    def only_in_scans(self, fact_id: str) -> bool:
        files = self.primary.get(fact_id, [])
        return bool(files) and all(f.format == "scan_jpg" for f in files)


def _allocate(total: int, weights: dict[str, float]) -> dict[str, int]:
    s = sum(weights.values()) or 1.0
    raw = {k: total * w / s for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    rest = total - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:rest]:
        out[k] += 1
    return out


def select_facts(ledger: CaseLedger, index: FactIndex, cfg: SynthConfig, rng: random.Random) -> list[Selection]:
    """Choose fact sets per question type according to the configured mix."""
    counts = _allocate(cfg.questions.per_case, cfg.questions.mix.model_dump())
    used: set[str] = set()
    present = [f for f in ledger.facts if index.primary.get(f.id) and not f.id.startswith("TITLE_")]
    selections: list[Selection] = []

    def take(pool: list[Fact], n: int) -> list[Fact]:
        pool = [f for f in pool if f.id not in used]
        rng.shuffle(pool)
        return pool[:n]

    def add(type_: str, facts: list[Fact]) -> None:
        for f in facts:
            used.add(f.id)
        files = [rf for f in facts for rf in index.primary.get(f.id, [])]
        selections.append(Selection(type_, facts, _INSTRUCTIONS[type_], files))

    az_used = 0
    exact_pool = []
    for f in present:
        if f.kind in _EXACT_KINDS and not index.only_in_scans(f.id):
            if f.id.startswith("AZ_"):
                if az_used >= 1:
                    continue
                az_used += 1
            exact_pool.append(f)
    for f in take(exact_pool, counts.get("exact_token", 0)):
        add("exact_token", [f])
    text_pool = [f for f in present if f.kind in _TEXT_KINDS and not index.only_in_scans(f.id)]
    for f in take(text_pool, counts.get("single_fact", 0)):
        add("single_fact", [f])
    scan_pool = [f for f in present if index.only_in_scans(f.id)]
    for f in take(scan_pool, counts.get("scan_ocr", 0)):
        add("scan_ocr", [f])

    for _ in range(counts.get("multi_doc", 0)):
        pool = [f for f in present if f.id not in used and not f.id.startswith(("AZ_", "COST_"))]
        rng.shuffle(pool)
        pair = None
        for i, a in enumerate(pool):
            for b in pool[i + 1:]:
                if not (index.primary_docs(a.id) & index.primary_docs(b.id)):
                    pair = [a, b]
                    break
            if pair:
                break
        if pair:
            add("multi_doc", pair)

    for _ in range(counts.get("timeline", 0)):
        dates = [f for f in present if f.kind == "date" and f.id not in used]
        rng.shuffle(dates)
        pair = next(([a, b] for i, a in enumerate(dates) for b in dates[i + 1:]
                     if not (index.primary_docs(a.id) & index.primary_docs(b.id)) and a.value != b.value), None)
        if pair:
            add("timeline", pair)

    title_docs = [d for d in ledger.documents if d.filename_only_fact_id and index.by_doc.get(d.id)]
    for d in title_docs[: counts.get("filename_only", 0)]:
        files = [f for f in index.by_doc[d.id] if f.kind == "primary"]
        other = [ledger.fact(fid) for fid in sorted(set(fid for f in files for fid in f.fact_ids))
                 if fid not in used and not fid.startswith(("AZ_", "COST_", "TITLE_"))]
        if not other:
            continue
        f2 = rng.choice(other)
        used.add(f2.id)
        selections.append(Selection("filename_only", [ledger.fact(d.filename_only_fact_id), f2],
                                    _INSTRUCTIONS["filename_only"], files))
    return selections


def _display(p: Optional[Identity]) -> str:
    if p is None:
        return ""
    return p.full_name if p.kind == "natural" else p.name


def _client_and_opponent(ledger: CaseLedger) -> tuple[str, str]:
    client = ledger.identity(ledger.client_person_id)
    opponent = next((p for p in ledger.identities if p.id == "P2"), None)
    return _display(client), _display(opponent)


def build_gold(
    *, cfg: SynthConfig, ledger: CaseLedger, index: FactIndex, sel: Selection, question: str, answer: str,
    nuggets: list[str], qid: str,
) -> GoldCase:
    """Assemble one gold case from a selection and the model's phrasing."""
    ctx = cfg.questions.quote_context_chars
    spans: list[GoldSpan] = []
    gold_docs: list[str] = []
    for fact in sel.facts:
        if fact.id.startswith("TITLE_"):
            continue
        for rf in index.planned_files(fact.id):
            if sel.type == "filename_only" and rf not in sel.files:
                continue
            found = find_quote(rf.text, fact.value, ctx, hints=f"{fact.label} {fact.description}")
            if not found:
                continue
            quote, prefix, suffix = found
            copies = [c.path for c in index.copies.get(fact.id, []) if c.of_doc_id == rf.of_doc_id]
            spans.append(GoldSpan(source=rf.path, quote=quote, prefix=prefix, suffix=suffix, fact_id=fact.id,
                                  equivalent_sources=copies))
            if rf.path not in gold_docs:
                gold_docs.append(rf.path)
    if sel.type == "filename_only":
        gold_docs = sorted({rf.path for rf in sel.files}) or gold_docs
    must_include = [f.value for f in sel.facts if f.kind in _EXACT_KINDS]
    must_not = sorted({v.distractor_value for f in sel.facts for v in index.versions.get(f.id, [])
                       if v.distractor_fact_id == f.id and v.distractor_value})
    formats = sorted({rf.format for rf in sel.files})
    tags = [ledger.area_id, sel.type, *formats]
    if any(index.copies.get(f.id) for f in sel.facts):
        tags.append("has_copies")
    if must_not:
        tags.append("has_draft_distractor")
    return GoldCase(
        id=qid, dataset_id=cfg.run.run_name, case_id=ledger.case_id, area_id=ledger.area_id, type=sel.type,  # type: ignore[arg-type]
        prompt=question.strip(), agent_mode=cfg.questions.default_agent_mode, tags=tags,
        expected_tools=list(cfg.questions.expected_tools), forbidden_tools=list(cfg.questions.forbidden_tools),
        gold_documents=gold_docs, gold_spans=spans, nuggets=nuggets, must_include=must_include,
        must_not_include=must_not, reference_answer=answer.strip(), fact_ids=[f.id for f in sel.facts],
    )


async def case_questions(
    *, llm: LLMClient, cfg: SynthConfig, ledger: CaseLedger, manifest: CaseManifest, all_texts: list[str]
) -> tuple[list[GoldCase], list[str]]:
    """Answerable and unanswerable questions for one case. Returns ``(gold, warnings)``."""
    warnings: list[str] = []
    rng = random.Random(_seed_int(cfg.corpus.random_seed, ledger.case_id, "questions"))
    index = FactIndex(ledger, manifest)
    selections = select_facts(ledger, index, cfg, rng)
    client, opponent = _client_and_opponent(ledger)
    gold: list[GoldCase] = []
    if selections:
        user = render(
            "questions_user", cfg.seeds.prompts_dir, case_title=ledger.title, internal_no=ledger.internal_file_number,
            client=client, opponent=opponent, selections=selections,
        )
        batch = await llm.structured("orchestrator", render("questions_system", cfg.seeds.prompts_dir), user,
                                     QuestionBatchOutput, tag=f"questions:{ledger.case_id}")
        if len(batch.questions) != len(selections):
            warnings.append(f"{ledger.case_id}: asked for {len(selections)} questions, got {len(batch.questions)}")
        for n, (sel, q) in enumerate(zip(selections, batch.questions), start=1):
            g = build_gold(cfg=cfg, ledger=ledger, index=index, sel=sel, question=q.question, answer=q.reference_answer,
                           nuggets=q.nuggets, qid=f"{ledger.case_id}-q{n:02d}")
            if not g.gold_documents:
                warnings.append(f"{g.id}: no gold document found, dropped")
                continue
            gold.append(g)

    n_un = cfg.questions.unanswerable_per_case
    if n_un:
        facts = [f for f in ledger.facts if not f.id.startswith("TITLE_")]
        user = render("unanswerable_user", cfg.seeds.prompts_dir, case_title=ledger.title,
                      internal_no=ledger.internal_file_number, client=client, opponent=opponent,
                      case_summary=ledger.summary, facts=facts, n=n_un + 1)
        batch_u = await llm.structured("orchestrator", render("questions_system", cfg.seeds.prompts_dir), user,
                                       UnanswerableBatchOutput, tag=f"unanswerable:{ledger.case_id}")
        corpus_lower = "\n".join(all_texts).lower()
        accepted = 0
        for q in batch_u.questions:
            hits = [t for t in q.absent_terms if t.strip() and t.lower() in corpus_lower]
            if hits:
                warnings.append(f"{ledger.case_id}: unanswerable question rejected, terms found in corpus: {hits}")
                continue
            accepted += 1
            gold.append(GoldCase(
                id=f"{ledger.case_id}-u{accepted:02d}", dataset_id=cfg.run.run_name, case_id=ledger.case_id,
                area_id=ledger.area_id, type="unanswerable", prompt=q.question, agent_mode=cfg.questions.default_agent_mode,
                tags=[ledger.area_id, "unanswerable"], expected_tools=list(cfg.questions.expected_tools),
                forbidden_tools=list(cfg.questions.forbidden_tools), gold_documents=[], gold_spans=[],
                nuggets=["Die Akte enthält dazu keine Information."], must_include=[], reference_answer=q.reason,
                unanswerable=True,
            ))
            if accepted >= n_un:
                break
    return gold, warnings


def cross_case_questions(cfg: SynthConfig, ledgers: list[CaseLedger], manifests: dict[str, CaseManifest]) -> list[GoldCase]:
    """Deterministic questions spanning several case files (courts and firm staff)."""
    out: list[GoldCase] = []
    if cfg.questions.cross_case <= 0 or len(ledgers) < 2:
        return out
    by_court: dict[str, list[tuple[CaseLedger, str]]] = defaultdict(list)
    for led in ledgers:
        for p in led.proceedings:
            by_court[p.court.name].append((led, f"AZ_{p.id}"))
    by_author: dict[str, list[CaseLedger]] = defaultdict(list)
    for led in ledgers:
        for d in led.documents:
            person = led.identity(d.author_id)
            if person and person.role.startswith("kanzlei_") and led not in by_author[person.full_name]:
                by_author[person.full_name].append(led)

    templates: list[tuple[str, list[tuple[CaseLedger, str]]]] = []
    for court, items in sorted(by_court.items(), key=lambda kv: -len(kv[1])):
        if len({led.case_id for led, _ in items}) >= 2:
            templates.append((f"In welchen unserer Akten ist das {court} befasst? Bitte jeweils mit Geschäftszahl.", items))
    for author, leds in sorted(by_author.items(), key=lambda kv: -len(kv[1])):
        if len(leds) >= 2:
            templates.append((f"Für welche Mandanten hat {author} in unseren Akten Schriftstücke verfasst?",
                              [(led, "") for led in leds]))

    for n, (prompt, items) in enumerate(templates[: cfg.questions.cross_case], start=1):
        spans, docs, must, nuggets, fact_ids = [], [], [], [], []
        for led, fid in items:
            manifest = manifests[led.case_id]
            index = FactIndex(led, manifest)
            client = _display(led.identity(led.client_person_id))
            if fid:
                fact = led.fact(fid)
                must.append(fact.value)
                fact_ids.append(fid)
                nuggets.append(f"{led.internal_file_number} ({client}): {fact.value}")
                rf = next(iter(index.primary.get(fid, [])), None)
                if rf:
                    found = find_quote(rf.text, fact.value, cfg.questions.quote_context_chars)
                    if found:
                        spans.append(GoldSpan(source=rf.path, quote=found[0], prefix=found[1], suffix=found[2], fact_id=fid))
                    docs.append(rf.path)
            else:
                person = led.identity(led.client_person_id)
                must.append(person.name if person else client)  # without academic title
                nuggets.append(f"{client} ({led.internal_file_number})")
                rf = next((f for f in manifest.files if f.kind == "primary"
                           and (p := led.identity(next(d.author_id for d in led.documents if d.id == f.of_doc_id)))
                           and p.role.startswith("kanzlei_")), None)
                if rf:
                    docs.append(rf.path)
        out.append(GoldCase(
            id=f"cross-q{n:02d}", dataset_id=cfg.run.run_name, case_id=None, area_id=None, type="cross_case",
            prompt=prompt, agent_mode=cfg.questions.default_agent_mode, tags=["cross_case"],
            expected_tools=list(cfg.questions.expected_tools), forbidden_tools=list(cfg.questions.forbidden_tools),
            gold_documents=docs, gold_spans=spans, nuggets=nuggets, must_include=must,
            reference_answer="; ".join(nuggets), fact_ids=fact_ids,
        ))
    return out


def date_label(iso: str) -> str:
    """German date for reports."""
    return format_date_de(iso)
