"""Configuration for the synthetic corpus generator.

All settings live in :class:`SynthConfig`. Effective configuration is built in
three layers, later layers winning:

1. the defaults declared on the models below,
2. one or more JSON config files (deep-merged in the given order),
3. ``--set dotted.path=value`` overrides from the command line.

Unknown keys are rejected (``extra="forbid"``) so typos fail loudly instead of
being silently ignored. API keys are never part of the config: only the names of
the environment variables that hold them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
OutputFormat = Literal["docx", "pdf", "doc", "eml", "scan_jpg", "txt"]


class _Strict(BaseModel):
    """Base model that rejects unknown keys."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class LLMRoleConfig(_Strict):
    """Model settings for one role (orchestrator or generator)."""

    model: str
    reasoning_effort: Optional[ReasoningEffort] = "medium"
    max_output_tokens: int = Field(default=32000, ge=256)
    timeout_s: float = Field(default=600.0, gt=0)


class PriceConfig(_Strict):
    """Optional USD price per one million tokens, used only for usage reports."""

    input_per_1m: float = 0.0
    output_per_1m: float = 0.0


class BudgetConfig(_Strict):
    """Hard limits that stop a run before it overspends."""

    max_requests: Optional[int] = Field(default=2000, ge=1)
    max_output_tokens_total: Optional[int] = Field(default=6_000_000, ge=1)
    prices: dict[str, PriceConfig] = Field(default_factory=dict)


class LLMConfig(_Strict):
    """OpenAI access and model roles."""

    provider: Literal["openai"] = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: list[str] = Field(default_factory=lambda: ["OPENAI_API_KEY", "LLM_API_KEY"])
    env_file: Optional[str] = ".env"
    orchestrator: LLMRoleConfig = Field(
        default_factory=lambda: LLMRoleConfig(model="gpt-6-astra", reasoning_effort="medium")
    )
    generator: LLMRoleConfig = Field(
        default_factory=lambda: LLMRoleConfig(model="gpt-6.1-sol", reasoning_effort="low")
    )
    max_retries: int = Field(default=4, ge=0)
    retry_backoff_s: float = Field(default=4.0, ge=0)
    concurrency: int = Field(default=4, ge=1, le=32)
    cache_responses: bool = True
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


class LawyerCounts(_Strict):
    """Size of the fictional firm."""

    partners: int = Field(default=2, ge=1)
    associates: int = Field(default=2, ge=0)
    assistants: int = Field(default=2, ge=0)


class FirmConfig(_Strict):
    """The fictional law firm. Unset fields are generated deterministically."""

    name: Optional[str] = None
    city: str = "Wien"
    postcode: Optional[str] = None
    street: Optional[str] = None
    email_domain: Optional[str] = None
    staff: LawyerCounts = Field(default_factory=LawyerCounts)
    internal_file_prefix: str = "AKT"


class DocumentCountRange(_Strict):
    """Planned documents per case (before duplicates and versions)."""

    min: int = Field(default=18, ge=3)
    max: int = Field(default=28, ge=3)

    @model_validator(mode="after")
    def _ordered(self) -> "DocumentCountRange":
        if self.max < self.min:
            raise ValueError("documents_per_case.max must be >= min")
        return self


class DuplicateConfig(_Strict):
    """Deliberate duplicates and versions, which real case files are full of."""

    exact_copy_rate: float = Field(default=0.12, ge=0, le=1)
    version_rate: float = Field(default=0.08, ge=0, le=1)
    copy_folders: list[str] = Field(default_factory=lambda: ["_Ablage", "Posteingang"])


class ScanConfig(_Strict):
    """How scanned paper documents are simulated."""

    dpi: int = Field(default=150, ge=72, le=600)
    max_rotation_deg: float = Field(default=1.2, ge=0, le=10)
    noise: float = Field(default=0.035, ge=0, le=0.5)
    blur_radius: float = Field(default=0.5, ge=0, le=5)
    jpeg_quality: int = Field(default=72, ge=20, le=100)
    max_pages: int = Field(default=4, ge=1)


class PdfConfig(_Strict):
    """PDF rendering fonts. Empty means auto-detect (Windows Arial, Linux DejaVu)."""

    font_regular: Optional[str] = None
    font_bold: Optional[str] = None
    font_size: float = Field(default=10.5, ge=6, le=16)


class CorpusConfig(_Strict):
    """Shape of the generated corpus."""

    locale: str = "de_AT"
    random_seed: int = 20261011
    cases_per_area: int = Field(default=1, ge=1)
    documents_per_case: DocumentCountRange = Field(default_factory=DocumentCountRange)
    date_from: str = "2024-09-01"
    date_to: str = "2026-09-30"
    format_weights: dict[OutputFormat, float] = Field(
        default_factory=lambda: {"docx": 0.34, "pdf": 0.30, "doc": 0.08, "scan_jpg": 0.10, "txt": 0.03, "eml": 0.15}
    )
    email_format_weights: dict[Literal["eml", "pdf", "txt"], float] = Field(
        default_factory=lambda: {"eml": 0.5, "pdf": 0.5}
    )
    doc_fallback: Literal["docx", "skip"] = "docx"
    folders: dict[str, str] = Field(
        default_factory=lambda: {
            "korrespondenz": "01_Korrespondenz",
            "schriftsatz": "02_Schriftsätze",
            "gericht": "03_Gericht",
            "beweis": "04_Beweismittel",
            "kosten": "05_Kosten",
            "intern": "06_Intern",
        }
    )
    filename_only_fact_rate: float = Field(default=0.05, ge=0, le=1)
    duplicates: DuplicateConfig = Field(default_factory=DuplicateConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    pdf: PdfConfig = Field(default_factory=PdfConfig)

    @field_validator("format_weights", "email_format_weights")
    @classmethod
    def _positive(cls, v: dict[str, float]) -> dict[str, float]:
        if not v or sum(v.values()) <= 0:
            raise ValueError("weights must contain at least one positive value")
        if any(w < 0 for w in v.values()):
            raise ValueError("weights must be >= 0")
        return v


class RisConfig(_Strict):
    """Public court decisions from RIS (CC BY 4.0, § 7 UrhG) used as seeds."""

    enabled: bool = True
    base_url: str = "https://data.bka.gv.at/ris/api/v2.6"
    min_interval_s: float = Field(default=2.0, ge=2.0)  # RIS OGD FAQ: max 0.5 requests/s
    user_agent: str = "RecallHub-synthetic-corpus/0.1"
    timeout_s: float = 60.0
    decisions_per_case: int = Field(default=2, ge=0, le=10)
    search_page_size: Literal["Ten", "Twenty", "Fifty"] = "Ten"
    max_seed_chars: int = Field(default=14000, ge=1000)


class SeedsConfig(_Strict):
    """Where legal areas and case seeds are read from (package data plus extra dirs)."""

    include_builtin: bool = True
    areas_dirs: list[str] = Field(default_factory=list)
    cases_dirs: list[str] = Field(default_factory=list)
    prompts_dir: Optional[str] = None  # templates here override the built-in ones with the same file name
    ris: RisConfig = Field(default_factory=RisConfig)


class QuestionMix(_Strict):
    """Relative weights of question types."""

    single_fact: float = 0.30
    exact_token: float = 0.25
    multi_doc: float = 0.20
    timeline: float = 0.10
    filename_only: float = 0.05
    scan_ocr: float = 0.10


class QuestionsConfig(_Strict):
    """Gold question generation."""

    per_case: int = Field(default=8, ge=1)
    unanswerable_per_case: int = Field(default=1, ge=0)
    cross_case: int = Field(default=3, ge=0)
    mix: QuestionMix = Field(default_factory=QuestionMix)
    expected_tools: list[str] = Field(
        default_factory=lambda: ["search_profile_documents", "search_all_sources"]
    )
    forbidden_tools: list[str] = Field(default_factory=lambda: ["web_search", "browse_web"])
    quote_context_chars: int = Field(default=40, ge=0, le=200)
    default_agent_mode: Optional[Literal["fast", "thinking"]] = None


class ValidationConfig(_Strict):
    """Which consistency checks are errors."""

    require_all_facts_in_text: bool = True
    check_rendered_text: bool = True
    name_blocklist_from_seeds: bool = True
    extra_blocked_names: list[str] = Field(default_factory=list)
    max_write_attempts: int = Field(default=3, ge=1)


class RunConfig(_Strict):
    """Where a run writes its artifacts."""

    output_root: str = "synthetic_runs"
    run_name: str = "kanzlei_v1"
    areas: Optional[list[str]] = None  # None = all known areas
    cases: Optional[list[str]] = None  # None = all seeds of the selected areas
    generate_missing_cases: bool = True  # create seeds automatically when an area has fewer than cases_per_area

    @property
    def run_dir(self) -> Path:
        return Path(self.output_root) / self.run_name


class SynthConfig(_Strict):
    """Root configuration."""

    version: int = 1
    run: RunConfig = Field(default_factory=RunConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    firm: FirmConfig = Field(default_factory=FirmConfig)
    corpus: CorpusConfig = Field(default_factory=CorpusConfig)
    seeds: SeedsConfig = Field(default_factory=SeedsConfig)
    questions: QuestionsConfig = Field(default_factory=QuestionsConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)


# ─── Loading and overrides ────────────────────────────────────────────────────


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``update`` into a copy of ``base``.

    Dicts are merged key by key; every other value (including lists) replaces
    the previous one.
    """
    out = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def parse_override(expr: str) -> tuple[list[str], Any]:
    """Parse ``dotted.path=value``. The value is JSON if it parses, else a string.

    Examples:
        ``llm.concurrency=8`` gives ``(["llm", "concurrency"], 8)``;
        ``run.areas=["mietrecht"]`` gives a list; ``firm.name=Kanzlei Huber`` a string.
    """
    if "=" not in expr:
        raise ValueError(f"override must look like key.path=value, got {expr!r}")
    path, raw = expr.split("=", 1)
    keys = [k for k in path.strip().split(".") if k]
    if not keys:
        raise ValueError(f"empty key in override {expr!r}")
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return keys, value


def apply_override(data: dict[str, Any], keys: list[str], value: Any) -> dict[str, Any]:
    """Return a copy of ``data`` with ``value`` set at the nested ``keys`` path."""
    out = dict(data)
    node = out
    for key in keys[:-1]:
        child = node.get(key)
        child = dict(child) if isinstance(child, dict) else {}
        node[key] = child
        node = child
    node[keys[-1]] = value
    return out


def _strip_comments(value: Any) -> Any:
    """Drop keys starting with ``_`` (used as comments in JSON config files)."""
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def load_config(paths: list[str | Path] | None = None, overrides: list[str] | None = None) -> SynthConfig:
    """Build the effective configuration.

    Args:
        paths: JSON config files, merged in order on top of the defaults.
        overrides: ``dotted.path=value`` strings applied last.

    Returns:
        The validated configuration.

    Raises:
        FileNotFoundError: If a config file does not exist.
        pydantic.ValidationError: If the merged configuration is invalid.
    """
    data: dict[str, Any] = SynthConfig().model_dump(mode="json")
    for path in paths or []:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"config file not found: {p}")
        data = deep_merge(data, _strip_comments(json.loads(p.read_text(encoding="utf-8"))))
    for expr in overrides or []:
        keys, value = parse_override(expr)
        data = apply_override(data, keys, value)
    return SynthConfig.model_validate(data)


def default_config_json() -> str:
    """Full default configuration as pretty JSON (used by ``init-config``)."""
    return json.dumps(SynthConfig().model_dump(mode="json"), indent=2, ensure_ascii=False)


def resolve_api_key(cfg: LLMConfig) -> str:
    """Find the API key from the configured env vars, loading ``env_file`` if needed.

    Existing environment variables win over the env file. The key itself is never
    logged or written anywhere.

    Raises:
        RuntimeError: If none of the configured variables is set.
    """
    if cfg.env_file and Path(cfg.env_file).is_file():
        from dotenv import dotenv_values

        file_values = dotenv_values(cfg.env_file)
    else:
        file_values = {}
    for name in cfg.api_key_env:
        value = os.environ.get(name) or file_values.get(name)
        if value:
            return value
    raise RuntimeError(
        f"No API key found. Set one of {cfg.api_key_env} in the environment"
        + (f" or in {cfg.env_file}" if cfg.env_file else "")
    )
