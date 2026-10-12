"""Seed registry: legal areas, case seeds and courts from JSON files.

Built-in data lives in ``data/``. Extra directories from the config are read
after it, so a user file with the same ``id`` replaces the built-in one. New
areas and cases need no code: drop a JSON file into a configured directory or
use the ``areas new`` / ``cases new`` CLI commands, which write templates.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from .config import DATA_DIR, RunConfig, SeedsConfig
from .models import AreaSpec, CaseSeed, Court

logger = logging.getLogger(__name__)


class SeedError(ValueError):
    """Raised for invalid or inconsistent seed files."""


def _json_files(dirs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        if d.is_dir():
            files.extend(sorted(p for p in d.glob("*.json") if not p.name.startswith("_")))
    return files


class SeedRegistry:
    """Everything the generator can be seeded with."""

    def __init__(self, cfg: SeedsConfig, extra_case_dirs: Iterable[Path] = ()):  # noqa: D107
        area_dirs = ([DATA_DIR / "areas"] if cfg.include_builtin else []) + [Path(d) for d in cfg.areas_dirs]
        case_dirs = ([DATA_DIR / "cases"] if cfg.include_builtin else []) + [Path(d) for d in cfg.cases_dirs]
        case_dirs += list(extra_case_dirs)
        self.areas: dict[str, AreaSpec] = {}
        self.cases: dict[str, CaseSeed] = {}
        self.courts: dict[str, Court] = {}
        self.sources: dict[str, Path] = {}
        for path in _json_files(area_dirs):
            area = self._load(path, AreaSpec)
            self.areas[area.id] = area
            self.sources[f"area:{area.id}"] = path
        for path in _json_files(case_dirs):
            case = self._load(path, CaseSeed)
            self.cases[case.id] = case
            self.sources[f"case:{case.id}"] = path
        court_files = [DATA_DIR / "reference" / "courts.json"] + [Path(d) / "courts.json" for d in cfg.areas_dirs]
        for path in court_files:
            if path.is_file():
                for c in json.loads(path.read_text(encoding="utf-8")).get("courts", []):
                    court = Court.model_validate(c)
                    self.courts[court.id] = court
        self._check()

    @staticmethod
    def _load(path: Path, model: type) -> object:
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - re-raised with the file name
            raise SeedError(f"{path}: {exc}") from exc

    def _check(self) -> None:
        for case in self.cases.values():
            if case.area not in self.areas:
                raise SeedError(f"case {case.id} refers to unknown area {case.area!r}")
        for area in self.areas.values():
            unknown = [c for c in area.courts if c not in self.courts]
            if unknown:
                raise SeedError(f"area {area.id} refers to unknown courts {unknown}")
            if not any(r.role == "mandant" for r in area.roles):
                raise SeedError(f"area {area.id} needs a role 'mandant'")

    def select(self, run: RunConfig) -> tuple[list[AreaSpec], list[CaseSeed]]:
        """Areas and case seeds selected by ``run.areas`` / ``run.cases``."""
        if run.areas:
            missing = [a for a in run.areas if a not in self.areas]
            if missing:
                raise SeedError(f"unknown areas {missing}; known: {sorted(self.areas)}")
            areas = [self.areas[a] for a in run.areas]
        else:
            areas = [self.areas[a] for a in sorted(self.areas)]
        area_ids = {a.id for a in areas}
        if run.cases:
            missing = [c for c in run.cases if c not in self.cases]
            if missing:
                raise SeedError(f"unknown cases {missing}; known: {sorted(self.cases)}")
            cases = [self.cases[c] for c in run.cases]
        else:
            cases = [c for c in sorted(self.cases.values(), key=lambda c: c.id) if c.area in area_ids]
        return areas, cases


AREA_TEMPLATE = {
    "id": "",
    "name": "",
    "description": "Worum geht es in diesem Rechtsgebiet, wen vertritt die Kanzlei, welcher Verfahrensablauf?",
    "procedure_outline": ["Schritt 1", "Schritt 2"],
    "courts": ["bg_innere_stadt"],
    "registers": ["C"],
    "statutes": [],
    "roles": [
        {"role": "mandant", "kind": "natural", "label": "Mandant/in"},
        {"role": "gegner", "kind": "natural", "label": "Gegenpartei"},
        {"role": "richter", "kind": "natural", "label": "Richter/in"},
    ],
    "document_types": [
        {"type": "klage", "label": "Klage", "category": "schriftsatz", "formats": ["pdf"], "required": True, "guidance": ""},
        {"type": "kostennote", "label": "Kostennote", "category": "kosten", "formats": ["pdf"], "required": True,
         "is_cost_note": True, "guidance": "Setze die Marke [[KOSTENTABELLE]] genau einmal."},
    ],
    "question_focus": [],
    "ris_queries": [{"suchworte": "", "gericht": "OGH"}],
    "streitwert_range": [3000.0, 30000.0],
    "style_notes": "",
}


def write_area_template(directory: Path, area_id: str, name: str) -> Path:
    """Write a new area JSON scaffold and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{area_id}.json"
    if path.exists():
        raise SeedError(f"{path} already exists")
    data = dict(AREA_TEMPLATE, id=area_id, name=name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_case_seed(directory: Path, seed: CaseSeed) -> Path:
    """Write a case seed JSON file and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{seed.id}.json"
    if path.exists():
        raise SeedError(f"{path} already exists")
    path.write_text(seed.model_dump_json(indent=2, exclude_defaults=False) + "\n", encoding="utf-8")
    return path
