"""Stage orchestration with resumable artifacts per run directory.

Run directory layout::

    <output_root>/<run_name>/
      config.effective.json   firm.json
      seeds/cases/*.json      seeds/ris/<case>.json      seeds/generated_cases/*.json
      ledger/<case>.json      ledger/<case>.warnings.json
      written/<case>/<doc>.json
      corpus/<Kanzlei>/Akten/...            (the files to ingest)
      manifest/<case>.json
      gold/questions.jsonl  gold/questions.json  gold/questions.yaml  gold/dataset.json
      reports/validation.json  reports/usage.json
      cache/llm/  cache/ris/  logs/llm_calls.jsonl
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from .config import SynthConfig, resolve_api_key
from .identities import IdentityFactory, extract_blocked_surnames
from .layout import render_case
from .llm import FakeLLMClient, LLMClient, OpenAILLMClient
from .models import CaseLedger, CaseManifest, CaseSeed, Firm, GoldCase, WrittenDocument
from .models import PlanOutput
from .planner import generate_case_seed, load_ledger, postprocess_plan, prepare_plan, save_ledger
from .questions import case_questions, cross_case_questions
from .ris import RisClient, RisDecision
from .seeds import SeedRegistry, write_case_seed
from .validate import validate_run
from .writer import load_written, save_written, write_document

logger = logging.getLogger(__name__)

STAGES = ["seeds", "plan", "write", "render", "questions", "validate"]


class Pipeline:
    """Runs the generator stages for one configuration."""

    def __init__(self, cfg: SynthConfig, llm: Optional[LLMClient] = None, contact: Optional[str] = None):  # noqa: D107
        self.cfg = cfg
        self.run_dir = cfg.run.run_dir
        self.contact = contact
        self._llm = llm
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "config.effective.json").write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        self.registry = SeedRegistry(cfg.seeds, extra_case_dirs=[self.run_dir / "seeds" / "generated_cases"])

    # ── helpers ──────────────────────────────────────────────────────────
    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            key = resolve_api_key(self.cfg.llm)
            self._llm = OpenAILLMClient(self.cfg.llm, key, self.run_dir / "cache" / "llm", self.run_dir / "logs" / "llm_calls.jsonl")
        return self._llm

    @property
    def corpus_root(self) -> Path:
        return self.run_dir / "corpus"

    def _path(self, *parts: str) -> Path:
        return self.run_dir.joinpath(*parts)

    def selected(self) -> tuple[list[Any], list[CaseSeed]]:
        return self.registry.select(self.cfg.run)

    def _fee_engine(self) -> Any:
        try:
            from .fees import FeeEngine
        except ImportError:
            logger.warning("fee engine not available; cost notes will have no computed table")
            return None
        return FeeEngine()

    def firm(self) -> Firm:
        path = self._path("firm.json")
        if path.is_file():
            return Firm.model_validate_json(path.read_text(encoding="utf-8"))
        f = self.cfg.firm
        firm = IdentityFactory(self.cfg.corpus.random_seed, self.cfg.corpus.locale).firm(
            f.name, f.city, f.postcode, f.street, f.email_domain, f.staff.partners, f.staff.associates, f.staff.assistants
        )
        path.write_text(firm.model_dump_json(indent=2), encoding="utf-8")
        return firm

    def decisions(self, case_id: str) -> list[RisDecision]:
        path = self._path("seeds", "ris", f"{case_id}.json")
        if not path.is_file():
            return []
        return [RisDecision.model_validate(d) for d in json.loads(path.read_text(encoding="utf-8"))]

    def blocked_surnames(self, cases: list[CaseSeed]) -> set[str]:
        names: set[str] = set(self.cfg.validation.extra_blocked_names)
        if self.cfg.validation.name_blocklist_from_seeds:
            texts = [d.text for c in cases for d in self.decisions(c.id)]
            names |= extract_blocked_surnames(texts)
        return names

    def ledgers(self, cases: list[CaseSeed]) -> list[CaseLedger]:
        out = []
        for c in cases:
            p = self._path("ledger", f"{c.id}.json")
            if p.is_file():
                out.append(load_ledger(p))
        return out

    def written(self, ledger: CaseLedger) -> dict[str, WrittenDocument]:
        out = {}
        for d in ledger.documents:
            p = self._path("written", ledger.case_id, f"{d.id}.json")
            if p.is_file():
                out[d.id] = load_written(p)
        return out

    def manifest(self, case_id: str) -> Optional[CaseManifest]:
        p = self._path("manifest", f"{case_id}.json")
        return CaseManifest.model_validate_json(p.read_text(encoding="utf-8")) if p.is_file() else None

    def write_usage(self) -> dict[str, Any]:
        """Usage of this process plus cumulative totals from the call log."""
        cumulative: dict[str, dict[str, int]] = {}
        log = self._path("logs", "llm_calls.jsonl")
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                m = cumulative.setdefault(rec.get("model", "?"), {"requests": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0})
                if rec.get("cache"):
                    m["cache_hits"] += 1
                elif "error" not in rec:
                    m["requests"] += 1
                    m["input_tokens"] += rec.get("input_tokens") or 0
                    m["output_tokens"] += rec.get("output_tokens") or 0
        report = {"this_process": self._llm.usage.report(self.cfg.llm.budget.prices) if self._llm else {}, "cumulative": cumulative}
        out = self._path("reports", "usage.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    # ── stages ───────────────────────────────────────────────────────────
    async def stage_seeds(self, force: bool = False) -> dict[str, Any]:
        """Generate missing case seeds and fetch RIS decisions."""
        areas, cases = self.selected()
        created = []
        if self.cfg.run.generate_missing_cases and not self.cfg.run.cases:
            for area in areas:
                existing = [c for c in self.registry.cases.values() if c.area == area.id]
                for n in range(len(existing), self.cfg.corpus.cases_per_area):
                    new_id = f"{area.id.split('_')[0]}_{n + 1:02d}g"
                    seed = await generate_case_seed(llm=self.llm, cfg=self.cfg, area=area, existing=existing, new_id=new_id)
                    write_case_seed(self._path("seeds", "generated_cases"), seed)
                    existing.append(seed)
                    created.append(new_id)
            if created:
                self.registry = SeedRegistry(self.cfg.seeds, extra_case_dirs=[self._path("seeds", "generated_cases")])
                areas, cases = self.selected()
        seed_dir = self._path("seeds", "cases")
        seed_dir.mkdir(parents=True, exist_ok=True)
        for c in cases:
            (seed_dir / f"{c.id}.json").write_text(c.model_dump_json(indent=2), encoding="utf-8")

        fetched: dict[str, int] = {}
        ris_cfg = self.cfg.seeds.ris
        if ris_cfg.enabled and ris_cfg.decisions_per_case > 0:
            client = RisClient(ris_cfg, self._path("cache", "ris"), contact=self.contact)
            used_ids: set[str] = set()
            try:
                for c in cases:
                    out_path = self._path("seeds", "ris", f"{c.id}.json")
                    if out_path.is_file() and not force:
                        decisions = self.decisions(c.id)
                        used_ids |= {d.document_id for d in decisions}
                        fetched[c.id] = len(decisions)
                        continue
                    area = self.registry.areas[c.area]
                    decisions: list[RisDecision] = []
                    for gz in c.ris_geschaeftszahlen:
                        for ref in client.by_geschaeftszahl(gz)[:1]:
                            d = client.fetch(ref)
                            if d and d.document_id not in used_ids:
                                decisions.append(d)
                                used_ids.add(d.document_id)
                    if c.use_ris_search:
                        for q in area.ris_queries:
                            if len(decisions) >= ris_cfg.decisions_per_case:
                                break
                            for ref in client.search(q):
                                doc_id = ref.get("Data", {}).get("Metadaten", {}).get("Technisch", {}).get("ID", "")
                                if doc_id in used_ids:
                                    continue
                                d = client.fetch(ref)
                                if d and len(d.text) > 1500:
                                    decisions.append(d)
                                    used_ids.add(d.document_id)
                                    break
                    decisions = decisions[: max(ris_cfg.decisions_per_case, len(c.ris_geschaeftszahlen))]
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(json.dumps([d.model_dump() for d in decisions], ensure_ascii=False, indent=1), encoding="utf-8")
                    fetched[c.id] = len(decisions)
            finally:
                client.close()
        return {"generated_cases": created, "ris_decisions": fetched}

    async def stage_plan(self, force: bool = False) -> dict[str, Any]:
        """Plan every selected case into a ledger."""
        _, cases = self.selected()
        firm = self.firm()
        factory = IdentityFactory(self.cfg.corpus.random_seed, self.cfg.corpus.locale, self.blocked_surnames(cases))
        for s in firm.staff:
            factory.used_names.add(s.name.lower())
            factory.used_surnames.add(s.name.split(" ")[-1].lower())
        replanned = {c.id for c in cases if force or not self._path("ledger", f"{c.id}.json").is_file()}
        for led in self.ledgers(cases):
            if led.case_id in replanned:
                continue  # its identities are regenerated; reserving them would change names and miss the cache
            for p in led.identities:
                factory.used_names.add(p.name.lower())
                factory.used_surnames.add(p.name.split(" ")[0 if p.kind == "legal" else -1].lower())
        fee_engine = self._fee_engine()
        results: dict[str, Any] = {}
        all_case_ids = sorted(self.registry.cases)
        prompts = []
        for c in cases:  # identities are assigned sequentially so names stay unique across cases
            if self._path("ledger", f"{c.id}.json").is_file() and not force:
                results[c.id] = "exists"
                continue
            prompts.append(prepare_plan(cfg=self.cfg, area=self.registry.areas[c.area], seed=c, firm=firm,
                                        courts=self.registry.courts, factory=factory, decisions=self.decisions(c.id)))
        plans = await asyncio.gather(
            *(self.llm.structured("orchestrator", p.system, p.user, PlanOutput, tag=f"plan:{p.seed.id}") for p in prompts),
            return_exceptions=True,
        )
        for p, plan in zip(prompts, plans):
            c = p.seed
            if isinstance(plan, BaseException):
                results[c.id] = {"error": f"{type(plan).__name__}: {plan}"}
                logger.error("planning %s failed: %s", c.id, plan)
                continue
            ledger, warnings = postprocess_plan(
                plan, area=p.area, seed=c, identities=p.identities, firm=firm, courts=self.registry.courts, cfg=self.cfg,
                factory=factory, fee_engine=fee_engine, case_index=all_case_ids.index(c.id),
                ris_sources=[d.citation for d in p.decisions],
            )
            path = self._path("ledger", f"{c.id}.json")
            save_ledger(ledger, path)
            self.invalidate_after("plan", c.id)
            self._path("ledger", f"{c.id}.warnings.json").write_text(json.dumps(warnings, ensure_ascii=False, indent=1), encoding="utf-8")
            results[c.id] = {"documents": len(ledger.documents), "facts": len(ledger.facts), "warnings": len(warnings)}
        self.write_usage()
        return results

    async def stage_write(self, force: bool = False) -> dict[str, Any]:
        """Write all planned documents (concurrently, bounded by ``llm.concurrency``)."""
        _, cases = self.selected()
        firm = self.firm()
        tasks = []
        for led in self.ledgers(cases):
            area = self.registry.areas[led.area_id]
            for d in led.documents:
                path = self._path("written", led.case_id, f"{d.id}.json")
                if path.is_file() and not force:
                    continue
                tasks.append((led, area, d, path))

        for led_id in {t[0].case_id for t in tasks}:
            self.invalidate_after("write", led_id)

        async def one(led: CaseLedger, area: Any, d: Any, path: Path) -> tuple[str, int, int]:
            w = await write_document(llm=self.llm, cfg=self.cfg, ledger=led, firm=firm, area=area, doc=d)
            save_written(w, path)
            return f"{led.case_id}/{d.id}", w.attempts, len(w.missing_fact_ids)

        results = await asyncio.gather(*(one(*t) for t in tasks), return_exceptions=True)
        errors = [str(r) for r in results if isinstance(r, BaseException)]
        done = [r for r in results if not isinstance(r, BaseException)]
        self.write_usage()
        if errors:
            logger.error("%d documents failed: %s", len(errors), errors[:3])
        return {"written": len(done), "failed": len(errors), "retries": sum(a - 1 for _, a, _ in done),
                "with_missing_facts": sum(1 for *_, m in done if m), "errors": errors[:10]}

    def stage_render(self, force: bool = False) -> dict[str, Any]:
        """Render written documents into the corpus folder tree."""
        _, cases = self.selected()
        firm = self.firm()
        results: dict[str, Any] = {}
        for led in self.ledgers(cases):
            mpath = self._path("manifest", f"{led.case_id}.json")
            if mpath.is_file() and not force:
                results[led.case_id] = "exists"
                continue
            manifest, warnings = render_case(self.cfg, led, firm, self.written(led), self.corpus_root)
            self.invalidate_after("render", led.case_id)
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mpath.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            results[led.case_id] = {"files": len(manifest.files), "warnings": warnings}
        return results

    async def stage_questions(self, force: bool = False) -> dict[str, Any]:
        """Generate gold questions for all rendered cases plus cross-case questions."""
        _, cases = self.selected()
        ledgers = self.ledgers(cases)
        manifests = {led.case_id: m for led in ledgers if (m := self.manifest(led.case_id))}
        all_texts = [f.text for m in manifests.values() for f in m.files]
        gold: list[GoldCase] = []
        warnings: list[str] = []
        todo = []
        for led in ledgers:
            if led.case_id not in manifests:
                continue
            gpath = self._path("gold", "cases", f"{led.case_id}.json")
            if gpath.is_file() and not force:
                gold.extend(GoldCase.model_validate(g) for g in json.loads(gpath.read_text(encoding="utf-8")))
            else:
                todo.append(led)
        results = await asyncio.gather(
            *(case_questions(llm=self.llm, cfg=self.cfg, ledger=led, manifest=manifests[led.case_id], all_texts=all_texts)
              for led in todo),
            return_exceptions=True,
        )
        for led, res in zip(todo, results):
            if isinstance(res, BaseException):
                warnings.append(f"{led.case_id}: question generation failed: {type(res).__name__}: {res}")
                continue
            g, w = res
            gpath = self._path("gold", "cases", f"{led.case_id}.json")
            gpath.parent.mkdir(parents=True, exist_ok=True)
            gpath.write_text(json.dumps([x.model_dump() for x in g], ensure_ascii=False, indent=1), encoding="utf-8")
            gold.extend(g)
            warnings.extend(w)
        gold.sort(key=lambda g: g.id)
        gold.extend(cross_case_questions(self.cfg, ledgers, manifests))
        self._write_gold(gold)
        self.write_usage()
        return {"questions": len(gold), "warnings": warnings}

    def _write_gold(self, gold: list[GoldCase]) -> None:
        out = self._path("gold")
        out.mkdir(parents=True, exist_ok=True)
        rows = [g.model_dump() for g in gold]
        (out / "questions.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        (out / "questions.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "questions.yaml").write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        firm = self.firm()
        dataset = {
            "id": self.cfg.run.run_name,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "firm": firm.name,
            "corpus_root": str(self.corpus_root.resolve()),
            "firm_folder": str((self.corpus_root / firm.name).resolve()),
            "models": {"orchestrator": self.cfg.llm.orchestrator.model, "generator": self.cfg.llm.generator.model},
            "questions": len(gold),
            "license_note": "Fictional corpus. Seeds: RIS OGD (CC BY 4.0), court decisions are free works under § 7 UrhG.",
        }
        (out / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    def stage_validate(self) -> dict[str, Any]:
        """Validate everything produced so far and write ``reports/validation.json``."""
        _, cases = self.selected()
        ledgers = self.ledgers(cases)
        written = {led.case_id: self.written(led) for led in ledgers}
        manifests = {led.case_id: m for led in ledgers if (m := self.manifest(led.case_id))}
        gpath = self._path("gold", "questions.json")
        gold = [GoldCase.model_validate(g) for g in json.loads(gpath.read_text(encoding="utf-8"))] if gpath.is_file() else []
        report = validate_run(self.cfg, ledgers, written, manifests, gold, self.corpus_root, self.blocked_surnames(cases))
        out = self._path("reports", "validation.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return report.as_dict()

    async def run(self, until: str = "validate", force: bool = False) -> dict[str, Any]:
        """Run stages in order up to and including ``until``."""
        results: dict[str, Any] = {}
        for stage in STAGES[: STAGES.index(until) + 1]:
            logger.info("stage %s", stage)
            if stage == "seeds":
                results[stage] = await self.stage_seeds(force)
            elif stage == "plan":
                results[stage] = await self.stage_plan(force)
            elif stage == "write":
                results[stage] = await self.stage_write(force)
            elif stage == "render":
                results[stage] = self.stage_render(force)
            elif stage == "questions":
                results[stage] = await self.stage_questions(force)
            elif stage == "validate":
                results[stage] = self.stage_validate()
        return results

    def status(self) -> dict[str, Any]:
        """Per-case progress of the stages."""
        _, cases = self.selected()
        out: dict[str, Any] = {}
        for c in cases:
            entry: dict[str, Any] = {"area": c.area, "ris": len(self.decisions(c.id))}
            lp = self._path("ledger", f"{c.id}.json")
            if lp.is_file():
                led = load_ledger(lp)
                entry["planned_documents"] = len(led.documents)
                entry["written"] = len(self.written(led))
            entry["rendered_files"] = len(m.files) if (m := self.manifest(c.id)) else 0
            gp = self._path("gold", "cases", f"{c.id}.json")
            entry["questions"] = len(json.loads(gp.read_text(encoding="utf-8"))) if gp.is_file() else 0
            out[c.id] = entry
        return out

    def invalidate_after(self, stage: str, case_id: str) -> None:
        """Delete artifacts of all stages after ``stage`` for one case, so they are rebuilt."""
        later = STAGES[STAGES.index(stage) + 1:]
        if "write" in later:
            shutil.rmtree(self._path("written", case_id), ignore_errors=True)
        if "render" in later:
            self._path("manifest", f"{case_id}.json").unlink(missing_ok=True)
        if "questions" in later:
            self._path("gold", "cases", f"{case_id}.json").unlink(missing_ok=True)


def fake_pipeline(cfg: SynthConfig, responder: Any) -> Pipeline:
    """Pipeline with a deterministic fake LLM (tests and dry runs)."""
    return Pipeline(cfg, llm=FakeLLMClient(responder))
