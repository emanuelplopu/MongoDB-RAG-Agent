"""Renderer tests and an end-to-end pipeline run with a deterministic fake LLM (no network)."""

from __future__ import annotations

import asyncio
import json
import random
import re
from pathlib import Path

import pytest

from backend.evaluation.synthetic_corpus import fsutil
from backend.evaluation.synthetic_corpus.config import load_config
from backend.evaluation.synthetic_corpus.llm import FakeLLMClient
from backend.evaluation.synthetic_corpus.models import (
    CaseSeedDraftOutput,
    CostItemOutput,
    DocumentDraft,
    EventOutput,
    FactOutput,
    PlannedDocumentOutput,
    PlanOutput,
    ProceedingOutput,
    QuestionBatchOutput,
    QuestionDraftOutput,
    UnanswerableBatchOutput,
    UnanswerableOutput,
)
from backend.evaluation.synthetic_corpus.pipeline import Pipeline
from backend.evaluation.synthetic_corpus.render import (
    Block,
    canonical_text,
    extract_text,
    render_docx,
    render_pdf,
    render_scan,
    render_txt,
)
from backend.evaluation.synthetic_corpus.config import PdfConfig, ScanConfig

# ─── renderers ───────────────────────────────────────────────────────────────

_BLOCKS = [
    Block("header", "Holl & Schwab Rechtsanwälte OG"),
    Block("title", "Aufforderungsschreiben"),
    Block("heading", "1. Forderung"),
    Block("para", "Die offene Forderung beträgt EUR 7.864,80 und ist seit 03.02.2025 fällig."),
    Block("bullet", "Rechnung RE-2024-0815 über EUR 7.864,80"),
    Block("table", rows=[["Datum", "Leistung", "TP", "Entlohnung", "ES", "ERV", "Summe"],
                         ["02.06.2025", "Klage", "TP3A", "EUR 346,60", "EUR 207,96", "EUR 5,00", "EUR 559,56"]]),
    Block("closing", "Mit freundlichen Grüßen"),
]


@pytest.mark.parametrize("fmt", ["docx", "pdf", "txt"])
def test_renderers_keep_values_extractable(tmp_path: Path, fmt: str) -> None:
    path = tmp_path / f"doc.{fmt}"
    if fmt == "docx":
        render_docx(_BLOCKS, path, title="T", author="A", doc_date="2025-02-03")
    elif fmt == "pdf":
        assert render_pdf(_BLOCKS, path, PdfConfig(), title="T", author="A") >= 1
    else:
        render_txt(_BLOCKS, path)
    text = extract_text(path)
    for value in ("EUR 7.864,80", "03.02.2025", "RE-2024-0815", "EUR 559,56", "Holl & Schwab"):
        assert value in re.sub(r"\s+", " ", text)


def test_canonical_text_and_scan(tmp_path: Path) -> None:
    text = canonical_text(_BLOCKS)
    assert "- Rechnung RE-2024-0815" in text and "TP3A | EUR 346,60" in text
    pages = render_scan(_BLOCKS, tmp_path / "scan", PdfConfig(), ScanConfig(), random.Random(1), title="T")
    assert len(pages) == 1 and pages[0][0].suffix == ".jpg" and pages[0][0].stat().st_size > 10_000
    assert "7.864,80" in pages[0][1]


# ─── fake LLM ────────────────────────────────────────────────────────────────


def _plan() -> PlanOutput:
    def fact(fid: str, kind: str, label: str, amount=None, d=None, text=None) -> FactOutput:
        return FactOutput(id=fid, kind=kind, label=label, amount_eur=amount, date_iso=d, text=text, description=label)

    def doc(did, dtype, title, d, author, recips, cat, fmt, facts, proc=None, email=False, costs=None) -> PlannedDocumentOutput:
        return PlannedDocumentOutput(id=did, doc_type=dtype, title=title, date_iso=d, author_id=author, recipient_ids=recips,
                                     category=cat, format=fmt, is_email=email, proceeding_id=proc, fact_ids=facts,
                                     summary=f"{title} mit den Fakten {facts}.", reference_doc_ids=[], cost_items=costs or [])

    costs = [
        CostItemOutput(kind="ratg", post="TP3A", service_date_iso="2025-03-01", bemessungsgrundlage_eur=7864.80,
                       description="Mahnklage", einheitssatz=True, einheitssatz_factor=2, erv="initial", barauslage_amount_eur=None),
        CostItemOutput(kind="ggg", post="TP1", service_date_iso="2025-03-01", bemessungsgrundlage_eur=7864.80,
                       description="Pauschalgebühr", einheitssatz=False, einheitssatz_factor=1, erv="none", barauslage_amount_eur=None),
    ]
    return PlanOutput(
        title="Tischlerei gegen {P2}", summary="Inkasso gegen {P2} beim {V1}.", streitwert_eur=7864.80,
        client_person_id="P1", extra_persons=[],
        proceedings=[
            ProceedingOutput(id="V1", court_id="bg_favoriten", court_register="C", year=2025, label="Mahnverfahren", judge_person_id="P4"),
            ProceedingOutput(id="V2", court_id="bg_favoriten", court_register="E", year=2025, label="Exekution", judge_person_id="P5"),
        ],
        facts=[
            fact("F1", "amount", "Kapitalforderung", amount=7864.80),
            fact("F2", "date", "Datum Aufforderungsschreiben", d="2025-02-03"),
            fact("F3", "date", "Datum Zahlungsbefehl", d="2025-03-14"),
            fact("F4", "reference", "Rechnungsnummer", text="RE-2024-0815"),
            fact("F5", "statement", "Einspruch", text="{P2} hat keinen Einspruch erhoben"),
            fact("F6", "amount", "Pfändbarer Betrag monatlich", amount=412.50),
            fact("F7", "party", "Drittschuldner", text="{P3} ist Arbeitgeber von {P2}"),
        ],
        events=[EventOutput(id="E1", date_iso="2025-03-14", label="Zahlungsbefehl", fact_ids=["F3"])],
        documents=[
            doc("D01", "rechnung", "Rechnung RE-2024-0815", "2025-01-20", "P1", ["P2"], "beweis", "pdf", ["F1", "F4"]),
            doc("D02", "aufforderungsschreiben", "Aufforderungsschreiben an {P2}", "2025-02-03", "K1", ["P2"], "korrespondenz", "docx", ["F1", "F2"]),
            doc("D03", "zahlungsbefehl", "Zahlungsbefehl", "2025-03-14", "V1", ["P2", "K1"], "gericht", "scan_jpg", ["F1", "F3"], proc="V1"),
            doc("D04", "email_mandant", "E-Mail der Mandantin", "2025-03-20", "P1", ["K1"], "korrespondenz", "eml", ["F4"], email=True),
            doc("D05", "aktenvermerk", "Aktenvermerk Rechtskraft", "2025-04-15", "K2", [], "intern", "txt", ["F5"]),
            doc("D06", "drittschuldnererklaerung", "Drittschuldnererklärung", "2025-05-10", "P3", ["V2"], "gericht", "pdf", ["F6", "F7"], proc="V2"),
            doc("D07", "kostennote", "Kostennote Mahnverfahren", "2025-06-01", "K1", ["P1"], "kosten", "pdf", [], proc="V1", costs=costs),
        ],
        outcome="Der Zahlungsbefehl wurde rechtskräftig. Die Gehaltsexekution läuft.",
    )


def _draft(user: str) -> DocumentDraft:
    section = user.split("## Required values (must appear verbatim)", 1)[1].split("\n## ", 1)[0]
    values = [line[2:].split(": ", 1) for line in section.splitlines() if line.startswith("- ")]
    body = ["Sehr geehrte Damen und Herren,", ""]
    body += [f"Zum Punkt {label} halten wir fest: {value}." for label, value in values]
    if "[[KOSTENTABELLE]]" in user:
        body += ["", "[[KOSTENTABELLE]]", ""]
    return DocumentDraft(title="Schreiben", header_lines=["Absender", "Empfänger"], body_markdown="\n".join(body),
                         closing_lines=["Mit freundlichen Grüßen"], email=None)


def _responder(role, system, user, schema, tag):
    if schema is PlanOutput:
        return _plan()
    if schema is DocumentDraft:
        return _draft(user)
    if schema is QuestionBatchOutput:
        n = user.count("### Selection")
        return QuestionBatchOutput(questions=[QuestionDraftOutput(question=f"Testfrage {i}?", reference_answer="Antwort",
                                                                  nuggets=["Fakt"]) for i in range(n)])
    if schema is UnanswerableBatchOutput:
        return UnanswerableBatchOutput(questions=[
            UnanswerableOutput(question="Gab es ein Berufungsurteil?", absent_terms=["Berufungsurteil"], reason="Keine Berufung."),
            UnanswerableOutput(question="Wie hoch war die Versicherung?", absent_terms=["Zebrastreifenversicherung"], reason="Nicht vorhanden."),
        ])
    if schema is CaseSeedDraftOutput:
        raise AssertionError("no case generation expected")
    raise AssertionError(f"unexpected schema {schema}")


def test_pipeline_end_to_end_with_fake_llm(tmp_path: Path) -> None:
    cfg = load_config([], [
        f"run.output_root={json.dumps(str(tmp_path))}", "run.run_name=t", 'run.cases=["inkasso_01"]',
        "seeds.ris.enabled=false", "llm.env_file=null", "corpus.duplicates.exact_copy_rate=1.0",
        "corpus.duplicates.version_rate=1.0", "corpus.filename_only_fact_rate=0.0", "questions.per_case=6",
        "questions.cross_case=0", "questions.unanswerable_per_case=1", 'corpus.email_format_weights={"eml": 1.0}',
    ])
    fake = FakeLLMClient(_responder)
    pipe = Pipeline(cfg, llm=fake)
    results = asyncio.run(pipe.run("validate"))
    report = results["validate"]
    assert report["ok"], report["errors"]

    ledger = pipe.ledgers(pipe.selected()[1])[0]
    assert all(p.case_number for p in ledger.proceedings)
    assert ledger.fact("F1").value == "EUR 7.864,80" and ledger.fact("F3").value == "14.03.2025"
    assert "{P" not in ledger.fact("F5").value
    docs = {d.id: d for d in ledger.documents}
    # case numbers exist only from the court's first own document on
    assert "V1" not in docs["D02"].known_proceeding_ids and "V1" in docs["D07"].known_proceeding_ids
    assert "AZ_V1" in docs["D03"].fact_ids and "AZ_V1" in docs["D07"].fact_ids
    assert "AZ_V2" not in docs["D06"].fact_ids  # V2 never acted itself; its first document cannot know the number
    cost_doc = next(d for d in ledger.documents if d.id == "D07")
    assert cost_doc.cost_note is not None and "COST_TOTAL_D07" in cost_doc.fact_ids

    manifest = pipe.manifest("inkasso_01")
    kinds = {f.kind for f in manifest.files}
    assert {"primary", "copy", "version"} <= kinds
    formats = {f.format for f in manifest.files}
    assert {"pdf", "docx", "scan_jpg", "eml", "txt"} <= formats
    for f in manifest.files:
        assert fsutil.is_file(pipe.corpus_root / f.path)

    gold = json.loads((cfg.run.run_dir / "gold" / "questions.json").read_text(encoding="utf-8"))
    assert any(g["unanswerable"] for g in gold)
    answerable = [g for g in gold if not g["unanswerable"]]
    assert answerable and all(g["gold_documents"] for g in answerable)
    assert all(s["quote"] for g in answerable for s in g["gold_spans"])
    versions = [f for f in manifest.files if f.kind == "version"]
    assert versions and all(v.distractor_value for v in versions)
    for g in answerable:
        for v in versions:
            if v.distractor_fact_id in g["fact_ids"]:
                assert v.distractor_value in g["must_not_include"]
    # a question built directly on an altered fact must forbid the draft value
    from backend.evaluation.synthetic_corpus.questions import FactIndex, Selection, build_gold

    v = versions[0]
    index = FactIndex(ledger, manifest)
    fact = ledger.fact(v.distractor_fact_id)
    g = build_gold(cfg=cfg, ledger=ledger, index=index, sel=Selection("exact_token", [fact], "", index.primary[fact.id]),
                   question="Wie hoch?", answer=fact.value, nuggets=[fact.value], qid="x")
    assert v.distractor_value in g.must_not_include and fact.value in g.must_include
    assert "has_draft_distractor" in g.tags

    # resumable: a second run reuses all artifacts without new LLM calls
    calls_before = len(fake.calls)
    asyncio.run(Pipeline(cfg, llm=fake).run("validate"))
    assert len(fake.calls) == calls_before
