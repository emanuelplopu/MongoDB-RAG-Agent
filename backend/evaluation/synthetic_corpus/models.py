"""Data models: seeds (areas, cases), the case ledger, documents, gold questions.

Models used as LLM structured outputs (``*Output`` / ``*Draft``) avoid dict
fields and numeric constraints so they convert to OpenAI strict JSON schemas.
Internal models may use richer types.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .config import OutputFormat

PersonKind = Literal["natural", "legal"]
FactKind = Literal["amount", "date", "reference", "party", "statement", "deadline", "legal_basis", "other"]
Category = Literal["korrespondenz", "schriftsatz", "gericht", "beweis", "kosten", "intern"]
QuestionType = Literal[
    "single_fact", "exact_token", "multi_doc", "timeline", "filename_only", "scan_ocr", "unanswerable", "cross_case"
]


# ─── Seeds (JSON files, user-extensible) ─────────────────────────────────────


class RoleSpec(BaseModel):
    """A party or participant every case of an area needs."""

    model_config = ConfigDict(extra="forbid")

    role: str
    kind: PersonKind = "natural"
    label: str
    optional: bool = False


class DocumentTypeSpec(BaseModel):
    """A document type typical for an area, used to guide planning and writing."""

    model_config = ConfigDict(extra="forbid")

    type: str
    label: str
    category: Category
    formats: list[OutputFormat] = Field(default_factory=list)
    required: bool = False
    is_email: bool = False
    is_cost_note: bool = False
    guidance: str = ""


class RisQuery(BaseModel):
    """One RIS Judikatur search used to find seed decisions for an area."""

    model_config = ConfigDict(extra="forbid")

    applikation: str = "Justiz"
    suchworte: str
    gericht: Optional[str] = None
    entscheidungsdatum_von: Optional[str] = None
    norm: Optional[str] = None


class AreaSpec(BaseModel):
    """A legal area (Rechtsgebiet). One JSON file per area under ``data/areas``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    procedure_outline: list[str]
    courts: list[str] = Field(default_factory=list, description="Court ids from data/reference/courts.json")
    registers: list[str] = Field(default_factory=list, description="Court registers used, e.g. C, Cg, E")
    statutes: list[str] = Field(default_factory=list)
    roles: list[RoleSpec]
    document_types: list[DocumentTypeSpec]
    question_focus: list[str] = Field(default_factory=list)
    ris_queries: list[RisQuery] = Field(default_factory=list)
    streitwert_range: tuple[float, float] = (3000.0, 30000.0)
    style_notes: str = ""


class RoleHint(BaseModel):
    """Per-case hints for how code names a role's identity."""

    model_config = ConfigDict(extra="forbid")

    gender: Optional[Literal["female", "male"]] = None
    name_word: Optional[str] = Field(default=None, description="Word used in company names, e.g. 'Tischlerei'")
    legal_form: Optional[str] = None
    vienna: Optional[bool] = None
    postcode: Optional[str] = None


class CaseSeed(BaseModel):
    """A case description used as seed for one generated case file."""

    model_config = ConfigDict(extra="forbid")

    id: str
    area: str
    title: str
    description: str
    client_role: str = "mandant"
    start_date: Optional[str] = None
    streitwert: Optional[float] = None
    documents_target: Optional[int] = None
    ris_geschaeftszahlen: list[str] = Field(default_factory=list)
    use_ris_search: bool = True
    extra_instructions: str = ""
    role_hints: dict[str, RoleHint] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


# ─── Identities (assigned by code, never by the model) ───────────────────────


class Identity(BaseModel):
    """A fictional person or organisation."""

    id: str
    role: str
    kind: PersonKind
    label: str
    name: str
    title: str = ""
    street: str = ""
    postcode: str = ""
    city: str = ""
    email: str = ""
    phone: str = ""
    iban: str = ""
    birth_date: str = ""
    company_register_no: str = ""
    description: str = ""

    @property
    def address(self) -> str:
        return f"{self.street}, {self.postcode} {self.city}".strip(", ")

    @property
    def full_name(self) -> str:
        return f"{self.title} {self.name}".strip()


class Firm(BaseModel):
    """The fictional law firm."""

    name: str
    street: str
    postcode: str
    city: str
    phone: str
    email_domain: str
    uid: str
    staff: list[Identity]

    @property
    def address(self) -> str:
        return f"{self.street}, {self.postcode} {self.city}"


class Court(BaseModel):
    """A court (real public institution) from reference data."""

    id: str
    name: str
    short: str
    type: str
    street: str = ""
    postcode: str = ""
    city: str = ""


# ─── Planner structured output ───────────────────────────────────────────────


class ExtraPersonOutput(BaseModel):
    """A participant the planner adds (name assigned later by code)."""

    id: str = Field(description="Unique id like X1, X2")
    role: str
    kind: PersonKind
    gender: Literal["female", "male", "unspecified"]
    label: str = Field(description="German role label, e.g. 'Zeugin', 'Sachverständiger'")
    description: str


class ProceedingOutput(BaseModel):
    """A court or authority proceeding; its case number is assigned by code."""

    id: str = Field(description="Unique id like V1")
    court_id: str
    court_register: str = Field(description="Court register abbreviation, e.g. C, Cg, Cga, E, Msch, or DSB for the authority")
    year: int
    label: str
    judge_person_id: Optional[str] = Field(description="Id of the judge from the identities or extra persons, or null")


class FactOutput(BaseModel):
    """An atomic fact. Exactly one of amount/date/text carries the value."""

    id: str = Field(description="Unique id like F1, F2")
    kind: FactKind
    label: str = Field(description="Short German label, e.g. 'Hauptforderung', 'Datum Zahlungsbefehl'")
    amount_eur: Optional[float] = Field(description="Set for kind=amount, else null")
    date_iso: Optional[str] = Field(description="YYYY-MM-DD, set for kind=date or deadline, else null")
    text: Optional[str] = Field(description="Value for all other kinds; may reference persons as {P_ID}")
    description: str


class EventOutput(BaseModel):
    """A dated event on the case timeline."""

    id: str
    date_iso: str
    label: str
    fact_ids: list[str]


class CostItemOutput(BaseModel):
    """One requested cost item; amounts are computed by the fee engine."""

    kind: Literal["ratg", "ggg", "barauslage"]
    post: str = Field(
        description="RATG post from the service mapping (TP1, TP2, TP3A, TP3B, TP3C, TP5, TP6, TP8); hearings and meetings "
        "with duration, e.g. 'TP3A:2h' or 'TP8:45min'. GGG: TP1 civil first instance incl. Mahnklage, TP2 appeal "
        "(Berufung), TP3 Revision, TP4 Exekution; labour cases TP1_ARB..TP4_ARB. barauslage: short text"
    )
    service_date_iso: str
    bemessungsgrundlage_eur: float
    description: str
    einheitssatz: bool
    einheitssatz_factor: int = Field(description="1 normally; 2 or 3 where the service mapping says so (e.g. Klage, Berufung)")
    erv: Literal["none", "initial", "further"]
    barauslage_amount_eur: Optional[float]


class PlannedDocumentOutput(BaseModel):
    """A document of the case file."""

    id: str = Field(description="Unique id like D01, D02 in chronological order")
    doc_type: str
    title: str
    date_iso: str
    author_id: str = Field(description="Person id, firm staff id, or proceeding id for court documents")
    recipient_ids: list[str]
    category: Category
    format: Literal["auto", "docx", "pdf", "doc", "eml", "scan_jpg", "txt"]
    is_email: bool
    proceeding_id: Optional[str]
    fact_ids: list[str] = Field(description="Facts whose values MUST appear verbatim in this document")
    summary: str = Field(description="2-4 sentences: what the document says")
    reference_doc_ids: list[str]
    cost_items: list[CostItemOutput] = Field(description="Only for cost notes, else empty")


class PlanOutput(BaseModel):
    """The orchestrator's case plan."""

    title: str
    summary: str
    streitwert_eur: float
    client_person_id: str
    extra_persons: list[ExtraPersonOutput]
    proceedings: list[ProceedingOutput]
    facts: list[FactOutput]
    events: list[EventOutput]
    documents: list[PlannedDocumentOutput]
    outcome: str


class CaseSeedDraftOutput(BaseModel):
    """A new case scenario invented by the orchestrator for an area."""

    title: str
    description: str
    start_date_iso: str
    streitwert_eur: float
    extra_instructions: str
    tags: list[str]


# ─── Ledger (single source of truth after post-processing) ───────────────────


class Fact(BaseModel):
    """A fact with its canonical display value, which must appear verbatim."""

    id: str
    kind: FactKind
    label: str
    value: str
    description: str = ""
    amount_eur: Optional[float] = None
    date_iso: Optional[str] = None
    origin: Literal["planner", "code"] = "planner"


class Proceeding(BaseModel):
    """A proceeding with its assigned case number (Geschäftszahl)."""

    id: str
    court: Court
    court_register: str
    year: int
    label: str
    case_number: str
    judge_person_id: Optional[str] = None


class CostLineView(BaseModel):
    """A computed cost line, as rendered into cost notes."""

    description: str
    post: str
    service_date: str
    base: str
    einheitssatz: str
    erv: str
    net: str


class CostNoteView(BaseModel):
    """A computed cost note with formatted amounts."""

    lines: list[CostLineView]
    fees_net: str
    ust: str
    court_fees: str
    barauslagen: str
    total: str
    table_ids: list[str]


class PlannedDocument(PlannedDocumentOutput):
    """Planner document plus code-assigned fields."""

    resolved_format: OutputFormat = "docx"
    known_proceeding_ids: list[str] = Field(default_factory=list)  # proceedings whose case number exists at this date
    filename: str = ""
    cost_note: Optional[CostNoteView] = None
    filename_only_fact_id: Optional[str] = None


class CaseLedger(BaseModel):
    """Everything known about one generated case."""

    case_id: str
    area_id: str
    seed: CaseSeed
    internal_file_number: str
    folder_name: str
    title: str
    summary: str
    streitwert_eur: float
    client_person_id: str
    identities: list[Identity]
    proceedings: list[Proceeding]
    facts: list[Fact]
    events: list[EventOutput]
    documents: list[PlannedDocument]
    outcome: str
    ris_sources: list[str] = Field(default_factory=list)

    def fact(self, fact_id: str) -> Fact:
        for f in self.facts:
            if f.id == fact_id:
                return f
        raise KeyError(fact_id)

    def identity(self, person_id: str) -> Optional[Identity]:
        return next((p for p in self.identities if p.id == person_id), None)


# ─── Writer structured output ────────────────────────────────────────────────


class EmailMetaOutput(BaseModel):
    """Email headers for documents that are emails."""

    subject: str
    sender_id: str
    recipient_ids: list[str]
    cc_ids: list[str]


class DocumentDraft(BaseModel):
    """A written document, before rendering."""

    title: str
    header_lines: list[str] = Field(description="Letterhead/court header lines, sender/recipient blocks, references")
    body_markdown: str = Field(description="Main text. Markdown headings (#), lists (-) and **bold** allowed")
    closing_lines: list[str] = Field(description="Signature block lines")
    email: Optional[EmailMetaOutput]


class WrittenDocument(BaseModel):
    """A draft plus the canonical plain text used for gold quotes."""

    doc_id: str
    draft: DocumentDraft
    text: str
    attempts: int
    missing_fact_ids: list[str] = Field(default_factory=list)


# ─── Rendering and manifest ──────────────────────────────────────────────────


class RenderedFile(BaseModel):
    """One file on disk."""

    path: str  # relative to the corpus root
    format: OutputFormat
    kind: Literal["primary", "copy", "version"]
    of_doc_id: str
    pages: int = 1
    page_index: Optional[int] = None  # for multi-page scans: 1-based page of the document
    note: str = ""
    text: str = ""  # canonical text of this file (for scans: text layer of the clean page)
    fact_ids: list[str] = Field(default_factory=list)  # ledger facts whose values occur in ``text``
    distractor_fact_id: Optional[str] = None  # versions: fact whose value was altered
    distractor_value: Optional[str] = None


class CaseManifest(BaseModel):
    """Files of one case."""

    case_id: str
    area_id: str
    folder: str
    files: list[RenderedFile]


# ─── Gold questions ──────────────────────────────────────────────────────────


class QuestionDraftOutput(BaseModel):
    """One question written by the orchestrator for a given fact selection."""

    question: str
    reference_answer: str
    nuggets: list[str]


class QuestionBatchOutput(BaseModel):
    """Questions for a batch of fact selections, in the same order."""

    questions: list[QuestionDraftOutput]


class UnanswerableOutput(BaseModel):
    """A plausible question whose answer is not in the corpus."""

    question: str
    absent_terms: list[str] = Field(description="Distinctive terms the answer would need; they must not occur in the case")
    reason: str


class UnanswerableBatchOutput(BaseModel):
    """Several unanswerable questions."""

    questions: list[UnanswerableOutput]


class GoldSpan(BaseModel):
    """A chunking-independent text quote selector (W3C TextQuoteSelector style)."""

    source: str
    quote: str
    prefix: str = ""
    suffix: str = ""
    fact_id: Optional[str] = None
    equivalent_sources: list[str] = Field(default_factory=list)
    grade: Literal[1, 2] = 2


class GoldCase(BaseModel):
    """A gold test case, compatible with the golden-harness plan."""

    id: str
    version: int = 1
    status: Literal["draft", "approved", "retired"] = "draft"
    dataset_id: str
    case_id: Optional[str]
    area_id: Optional[str]
    type: QuestionType
    prompt: str
    agent_mode: Optional[Literal["fast", "thinking"]] = None
    tags: list[str] = Field(default_factory=list)
    expected_tools: list[str]
    forbidden_tools: list[str]
    gold_documents: list[str]
    gold_spans: list[GoldSpan]
    nuggets: list[str]
    must_include: list[str]
    must_not_include: list[str] = Field(default_factory=list)
    reference_answer: Optional[str]
    unanswerable: bool = False
    fact_ids: list[str] = Field(default_factory=list)
